#!/usr/bin/python3
"""
Webhook and status-check CGI for BTG Pactual Pix payments.

This CGI handles three distinct request types:

  1. **BTG Webhook** (POST with JSON body):
     Receives ``instant-collections.paid`` events from BTG and marks the
     corresponding payment as paid in BILLmanager. The webhook secret is
     validated via the ``Authorization`` header.

  2. **Frontend Polling** (GET ``?elid=X&check=1``):
     Returns a JSON response ``{"paid": true/false}`` for the QR code page's
     auto-polling JavaScript.

  3. **Manual Redirect** (GET ``?elid=X``):
     When the customer clicks "I already paid", checks the collection status
     and redirects to the Success, Pending, or Fail page.
"""

import json
import os
import sys
from typing import Optional

sys.path.insert(0, "/usr/local/mgr5/lib/python")

import billmgr.logger as logging
import billmgr.payment as payment
from billmgr.modules.paymentcgi import PageType, PaymentCgi, PaymentCgiType, run_cgi

from btgpix.enums import CollectionStatus
from btgpix.utils import (
    api_from_xmlparams,
    escape_html,
    find_payment_by_id,
    find_payment_by_collection_id,
)

_LOG = "btgpixwebhook"
logging.init_logging(_LOG)
log = logging.get_logger(_LOG)

import hmac

import billmgr.db

from btgpix.utils import MODULE_NAME

#: The BTG webhook event name for a successful Pix payment.
_PAID_EVENT = "instant-collections.paid"


def _validate_webhook_auth(auth_header: str) -> bool:
    """Validate the webhook Authorization header against stored secrets.

    BTG sends the webhook secret in the ``Authorization`` header. We compare
    it against the ``webhook_secret`` field stored in each BTG paymethod.

    Args:
        auth_header: Raw ``Authorization`` header value from the request.

    Returns:
        True if the header matches any configured paymethod's webhook secret.
    """
    if not auth_header:
        return False

    rows = billmgr.db.db_query(
        f"SELECT xmlparams FROM paymethod WHERE module = '{MODULE_NAME}'"
    )
    for row in rows:
        try:
            from xml.etree import ElementTree
            xml = ElementTree.fromstring(row.as_str("xmlparams"))
            secret = xml.findtext("webhook_secret", "")
            if secret and hmac.compare_digest(auth_header, secret):
                return True
        except Exception:
            continue

    return False


def _json_response(data: dict, status: int = 200) -> None:
    """Write a JSON HTTP response to stdout.

    Args:
        data:   Dictionary to serialize as JSON.
        status: HTTP status code (default 200).
    """
    print(f"Status: {status}")
    print("Content-Type: application/json\n")
    print(json.dumps(data))


def _mark_paid_if_pending(
    pay_id: int, current_status: int, external_id: str, source: str
) -> bool:
    """Mark a payment as paid only if it is still in the ``psInPay`` state.

    This guard prevents double-processing when both the webhook and polling
    detect the payment simultaneously.

    Args:
        pay_id:         BILLmanager payment ID.
        current_status: Current payment status from the database.
        external_id:    BTG collection UUID (stored as externalid).
        source:         Origin of the status change (for logging/audit).

    Returns:
        True if the payment was marked as paid, False if already processed.
    """
    if current_status == int(payment.PaymentStatus.psInPay.value):
        payment.set_paid(pay_id, info=f"pix_{source}", external_id=external_id)
        log.info(f"Payment {pay_id} marked PAID via {source}")
        return True
    log.info(f"Payment {pay_id} already processed (status={current_status})")
    return False


class BTGPixWebhookCgi(PaymentCgi):
    """CGI handler for webhook events and payment status checks."""

    def __init__(self) -> None:
        PaymentCgi.__init__(self)

    def cgi_type(self) -> PaymentCgiType:
        return PaymentCgiType.Payment

    def parse_input(self) -> None:
        """Parse CGI input from query string and request body.

        Extracts:
          - ``_elid``: Payment ID from query string.
          - ``_check``: Whether this is a polling request.
          - ``_body``: Parsed JSON body (for webhook POSTs).
          - ``_auth_header``: Authorization header (for webhook validation).
        """
        self._elid: str = self.input.get("elid", "")
        self._check: bool = self.input.get("check", "") == "1"
        self._body: Optional[dict] = None
        self._auth_header: str = os.environ.get("HTTP_AUTHORIZATION", "")

        try:
            length = int(os.environ.get("CONTENT_LENGTH", 0))
            if length > 0:
                self._body = json.loads(sys.stdin.read(length))
        except (ValueError, json.JSONDecodeError):
            log.warning("Failed to parse request body as JSON")

    def elid(self) -> str:
        return str(self._elid)

    def process(self) -> None:
        """Route the request to the appropriate handler."""
        # 1. BTG webhook (POST with event in body)
        if self._body and isinstance(self._body.get("event"), str):
            if self._body["event"].startswith("instant-collections."):
                self._handle_webhook()
                return

        # 2. Frontend polling (GET ?check=1)
        if self._elid and self._check:
            self._handle_check()
            return

        # 3. Manual redirect (GET ?elid=X)
        if self._elid:
            self._handle_redirect()
            return

        _json_response({"error": "missing elid"}, 400)

    # ── Webhook Handler ──────────────────────────────────────────

    def _handle_webhook(self) -> None:
        """Process an ``instant-collections.paid`` event from BTG.

        Validates the webhook secret, ignores non-payment events,
        looks up the payment by collection ID and marks it as paid.
        """
        if not _validate_webhook_auth(self._auth_header):
            log.warning("Webhook rejected: invalid or missing Authorization header")
            _json_response({"error": "unauthorized"}, 401)
            return

        event = self._body.get("event", "")
        data = self._body.get("data", {})
        log.info(f"Webhook received: event={event}")

        if event != _PAID_EVENT:
            log.info(f"Ignoring non-payment event: {event}")
            _json_response({"status": "ignored"})
            return

        # BTG may send the collection ID under different field names
        collection_id = data.get("collectionId", data.get("id", ""))
        if not collection_id:
            log.warning("Webhook payload missing collection ID")
            _json_response({"status": "no_id"})
            return

        try:
            pay = find_payment_by_collection_id(collection_id)
        except ValueError:
            log.warning(f"Invalid collection ID format in webhook: {collection_id}")
            _json_response({"status": "invalid_id"}, 400)
            return

        if pay is None:
            log.warning(f"No BILLmanager payment found for collection {collection_id}")
            _json_response({"status": "not_found"})
            return

        _mark_paid_if_pending(
            pay.as_int("id"), pay.as_int("status"), collection_id, "webhook"
        )
        _json_response({"status": "ok"})

    # ── Polling Handler ──────────────────────────────────────────

    def _handle_check(self) -> None:
        """Return JSON ``{"paid": bool, "status": str}`` for frontend polling.

        Queries the BTG API for the current collection status and, if paid,
        updates the BILLmanager payment record.
        """
        try:
            pay = find_payment_by_id(self._elid)
            collection_id = pay.as_str("externalid")
            api = api_from_xmlparams(pay.as_str("xmlparams"), pay.as_int("paymethod_id"))

            status = api.get_collection_status(collection_id)
            paid = status == CollectionStatus.PAID

            if paid:
                _mark_paid_if_pending(
                    pay.as_int("id"), pay.as_int("status"), collection_id, "poll"
                )

            _json_response({"paid": paid, "status": status})

        except Exception as e:
            log.error(f"Polling check error for elid={self._elid}: {e}")
            _json_response({"paid": False, "error": str(e)})

    # ── Manual Redirect Handler ──────────────────────────────────

    def _handle_redirect(self) -> None:
        """Customer clicked "I already paid" — verify and redirect.

        Checks the collection status on BTG and redirects the customer to:
          - **Success** page if the collection is paid.
          - **Pending** page if the collection is still active.
          - **Fail** page if the collection is canceled, failed, or on error.
        """
        try:
            pay = find_payment_by_id(self._elid)
            collection_id = pay.as_str("externalid")
            api = api_from_xmlparams(pay.as_str("xmlparams"), pay.as_int("paymethod_id"))

            status = api.get_collection_status(collection_id)

            if status == CollectionStatus.PAID:
                _mark_paid_if_pending(
                    pay.as_int("id"), pay.as_int("status"), collection_id, "manual"
                )
                self.redirect_to_url(self.get_page(PageType.Success))

            elif status in (CollectionStatus.ACTIVE, CollectionStatus.CREATED):
                self.redirect_to_url(self.get_page(PageType.Pending))

            else:
                self.redirect_to_url(self.get_page(PageType.Fail))

        except Exception as e:
            log.error(f"Manual check error for elid={self._elid}: {e}")
            self.redirect_to_url(self.get_page(PageType.Fail))


if __name__ == "__main__":
    run_cgi(BTGPixWebhookCgi)
