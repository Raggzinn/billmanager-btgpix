#!/usr/bin/python3
"""
BTG Pactual Pix payment module for BILLmanager 6.

This is the main paymethod module registered as ``pmbtgpix``. It implements:

  - **pm_validate**: Validates required fields when creating or editing the
    payment method in the admin panel.
  - **check_pay**: Periodically polls the BTG API for pending payments and
    updates their status in BILLmanager (paid, canceled, or expired).

The payment CGI (``btgpixpayment``) handles the customer-facing flow,
and the webhook CGI (``btgpixwebhook``) handles real-time notifications.
"""

import argparse
import sys
import traceback
from datetime import datetime, timedelta
from typing import List, Dict
from xml.etree import ElementTree

sys.path.insert(0, "/usr/local/mgr5/lib/python")

import billmgr.db
import billmgr.exception
import billmgr.logger as logging
import billmgr.session as session
from billmgr import payment
from billmgr.modules.paymethod import PaymethodModule, Feature, Param

from btgpix.api import BTGPixAPI
from btgpix.enums import CollectionStatus, PENDING_STATUSES
from btgpix.utils import MODULE_NAME

MODULE = "pmbtgpix"
logging.init_logging(MODULE)
log = logging.get_logger(MODULE)

#: Number of days after which a pending payment is considered expired.
MAX_PENDING_DAYS = 3

#: Required fields that must be filled before saving the paymethod.
REQUIRED_FIELDS = ("client_id", "client_secret", "company_id", "pix_key")


def _save_refreshed_tokens(
    xml_str: str, access_token: str, refresh_token: str
) -> None:
    """Persist refreshed tokens back to the paymethod record.

    Args:
        xml_str:       Original xmlparams string (used to locate the row).
        access_token:  New access token.
        refresh_token: New refresh token.
    """
    xml = ElementTree.fromstring(xml_str)
    for tag, value in [("access_token", access_token), ("refresh_token", refresh_token)]:
        el = xml.find(tag)
        if el is None:
            el = ElementTree.SubElement(xml, tag)
        el.text = value

    new_xml = ElementTree.tostring(xml, encoding="unicode").replace("'", "''")
    old_xml = xml_str.replace("'", "''")

    billmgr.db.db_query(
        f"UPDATE paymethod SET xmlparams = '{new_xml}'"
        f" WHERE module = '{MODULE_NAME}' AND xmlparams = '{old_xml}'"
    )
    log.info("Persisted refreshed tokens to paymethod")


def _api_from_xml(xml: ElementTree.Element, xml_str: str = "") -> BTGPixAPI:
    """Build a :class:`BTGPixAPI` instance from paymethod XML params.

    Args:
        xml:     Parsed ``ElementTree.Element`` of the paymethod's ``xmlparams``.
        xml_str: Original XML string, used for token persistence callback.

    Returns:
        Configured API client with token refresh persistence.
    """
    callback = None
    if xml_str:
        callback = lambda at, rt: _save_refreshed_tokens(xml_str, at, rt)

    return BTGPixAPI(
        client_id=xml.findtext("client_id", ""),
        client_secret=xml.findtext("client_secret", ""),
        company_id=xml.findtext("company_id", ""),
        pix_key=xml.findtext("pix_key", ""),
        access_token=xml.findtext("access_token", ""),
        refresh_token=xml.findtext("refresh_token", ""),
        sandbox=xml.findtext("sandbox", "off") == "on",
        on_token_refresh=callback,
    )


class BTGPixModule(PaymethodModule):
    """BILLmanager paymethod module for BTG Pactual Pix payments."""

    def __init__(self) -> None:
        super().__init__()

        # Customer is redirected to the payment CGI page (QR code).
        self._add_feature(Feature.REDIRECT)
        # No per-client payment profiles needed.
        self._add_feature(Feature.NOT_PROFILE)
        # Pix does not support refunds through this integration.
        self._add_feature(Feature.NOREFUND)

        self._add_callable_feature(Feature.CHECKPAY, self.check_pay)
        self._add_callable_feature(Feature.PMVALIDATE, self.pm_validate)

        self._add_param(Param.PAYMENT_SCRIPT, "/mancgi/btgpixpayment")

    def _on_raise_exception(
        self, args: argparse.Namespace, err: billmgr.exception.XmlException
    ) -> None:
        print(err.as_xml())

    # ── Validation ───────────────────────────────────────────────

    def pm_validate(self) -> None:
        """Validate that all required fields are present in the paymethod form.

        Called by BILLmanager when the admin saves the payment method.
        Raises an ``XmlException`` with the appropriate error key if any
        required field is empty.
        """
        log.info("pm_validate started")
        xml = session.get_input_xml()

        for field in REQUIRED_FIELDS:
            if not xml.findtext(field, "").strip():
                raise billmgr.exception.XmlException(f"missing_{field}")

        log.info("pm_validate passed")

    # ── Check Pay (Polling) ──────────────────────────────────────

    def check_pay(self) -> None:
        """Poll the BTG API for all pending payments and update their status.

        BILLmanager calls this method periodically. It queries all payments
        with ``status = psInPay`` that were created within the last
        :data:`MAX_PENDING_DAYS` days, groups them by paymethod credentials,
        and checks each one against the BTG API.
        """
        log.info("check_pay started")

        cutoff = (datetime.today() - timedelta(days=MAX_PENDING_DAYS)).strftime("%Y-%m-%d")

        rows: List[billmgr.db.Record] = billmgr.db.db_query(
            "SELECT p.id, p.paymethodamount, p.externalid, pm.xmlparams,"
            "       p.number, p.createdate"
            " FROM payment p"
            " JOIN paymethod pm ON p.paymethod = pm.id"
            f" WHERE pm.module = '{MODULE}'"
            f"   AND p.status = {payment.PaymentStatus.psInPay.value}"
            f"   AND p.createdate >= '{cutoff}'"
            " ORDER BY pm.xmlparams"
        )

        if not rows:
            log.info("No pending payments found")
            return

        # Group by xmlparams to reuse the same API session per paymethod
        groups: Dict[str, List[billmgr.db.Record]] = {}
        for row in rows:
            key = row.as_str("xmlparams")
            groups.setdefault(key, []).append(row)

        for xml_str, payments in groups.items():
            try:
                params = ElementTree.fromstring(xml_str)
                api = _api_from_xml(params, xml_str=xml_str)
            except Exception as e:
                log.error(f"Failed to initialize API for paymethod group: {e}")
                continue

            for pay in payments:
                self._check_single_payment(api, pay)

    def _check_single_payment(self, api: BTGPixAPI, pay: billmgr.db.Record) -> None:
        """Check a single payment's status on BTG and update BILLmanager.

        Args:
            api: Authenticated BTG API client.
            pay: Database record for the payment being checked.
        """
        pid = pay.as_int("id")
        collection_id = pay.as_str("externalid")

        if not collection_id:
            log.warning(f"Payment {pid} has no externalid, skipping")
            return

        try:
            status = api.get_collection_status(collection_id)
            log.info(f"Payment {pid} | collection={collection_id} | status={status}")

            if status == CollectionStatus.PAID:
                payment.set_paid(pid, info="pix_paid", external_id=collection_id)
                log.info(f"Payment {pid} marked as PAID")

            elif status in {CollectionStatus.CANCELED, CollectionStatus.FAILED}:
                payment.set_canceled(
                    pid, info=f"pix_{status.lower()}", external_id=collection_id
                )
                log.info(f"Payment {pid} marked as CANCELED ({status})")

            elif status in PENDING_STATUSES:
                created = datetime.strptime(
                    pay.as_str("createdate"), "%Y-%m-%d %H:%M:%S"
                )
                age_days = (datetime.today() - created).days
                if age_days > MAX_PENDING_DAYS:
                    payment.set_canceled(
                        pid, info="pix_expired", external_id=collection_id
                    )
                    log.info(f"Payment {pid} expired after {age_days} days")

        except Exception as e:
            log.error(
                f"Error checking payment {pid}: {e}\n{traceback.format_exc()}"
            )


if __name__ == "__main__":
    BTGPixModule().run()
