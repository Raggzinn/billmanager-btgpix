"""
Shared utilities for the BTG Pix BILLmanager module.

Centralizes paymethod parameter parsing, payment lookups, and HTML escaping
to avoid duplication across the CGI scripts.

Note on SQL:
    BILLmanager's ``billmgr.db`` module uses raw SQL strings (no prepared
    statements). This matches the pattern used by ISPsystem's own modules
    (e.g. NOWPayments). We cast IDs to ``int()`` to prevent injection on
    numeric fields, and use the module name as a constant.
"""

import html as _html_module
import re
from typing import Optional
from xml.etree import ElementTree

import billmgr.db

from btgpix.api import BTGPixAPI

#: Module identifier used in the ``paymethod.module`` database column.
MODULE_NAME = "pmbtgpix"

#: Pattern for validating UUID strings (BTG collection IDs).
_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)


def _persist_tokens(
    paymethod_id: int, xmlparams: str, access_token: str, refresh_token: str
) -> None:
    """Save refreshed OAuth2 tokens back to the paymethod's xmlparams in the DB.

    Called automatically by :class:`BTGPixAPI` when a token refresh occurs,
    ensuring the new tokens survive across CGI invocations.

    Uses ``paymethod.id`` instead of matching on ``xmlparams`` content to avoid
    race conditions when multiple processes refresh tokens simultaneously.

    Args:
        paymethod_id:  Paymethod row ID for the WHERE clause.
        xmlparams:     Current XML string (tokens are merged into this).
        access_token:  New access token from the refresh response.
        refresh_token: New refresh token from the refresh response.
    """
    xml = ElementTree.fromstring(xmlparams)

    for tag, value in [("access_token", access_token), ("refresh_token", refresh_token)]:
        el = xml.find(tag)
        if el is None:
            el = ElementTree.SubElement(xml, tag)
        el.text = value

    new_xmlparams = ElementTree.tostring(xml, encoding="unicode")
    escaped = new_xmlparams.replace("'", "''")

    billmgr.db.db_query(
        f"UPDATE paymethod SET xmlparams = '{escaped}'"
        f" WHERE id = {int(paymethod_id)}"
    )


def api_from_xmlparams(xmlparams: str, paymethod_id: int = 0) -> BTGPixAPI:
    """Build a :class:`BTGPixAPI` instance from a paymethod's XML params string.

    Includes an ``on_token_refresh`` callback so that tokens refreshed via
    automatic 401 retry are persisted back to the database.

    Args:
        xmlparams:    Raw XML string stored in ``paymethod.xmlparams``.
        paymethod_id: Paymethod row ID for safe token persistence.

    Returns:
        Configured BTGPixAPI ready to make API calls.
    """
    p = ElementTree.fromstring(xmlparams)

    callback = None
    if paymethod_id:
        callback = lambda at, rt: _persist_tokens(paymethod_id, xmlparams, at, rt)

    return BTGPixAPI(
        client_id=p.findtext("client_id", ""),
        client_secret=p.findtext("client_secret", ""),
        company_id=p.findtext("company_id", ""),
        pix_key=p.findtext("pix_key", ""),
        access_token=p.findtext("access_token", ""),
        refresh_token=p.findtext("refresh_token", ""),
        sandbox=p.findtext("sandbox", "off") == "on",
        on_token_refresh=callback,
    )


def api_from_dict(params: dict) -> BTGPixAPI:
    """Build a :class:`BTGPixAPI` from a flat dict (e.g. ``paymethod_params``).

    Used by the payment CGI where BILLmanager provides params as a dictionary.
    Note: token persistence on refresh is not wired here because the payment
    CGI creates short-lived collections; the ``check_pay`` polling (which uses
    ``api_from_xmlparams``) handles long-lived token management.

    Args:
        params: Dictionary with keys matching the paymethod XML fields.

    Returns:
        Configured BTGPixAPI ready to make API calls.
    """
    return BTGPixAPI(
        client_id=params["client_id"],
        client_secret=params["client_secret"],
        company_id=params["company_id"],
        pix_key=params["pix_key"],
        access_token=params.get("access_token", ""),
        refresh_token=params.get("refresh_token", ""),
        sandbox=params.get("sandbox", "off") == "on",
    )


def find_payment_by_id(elid: str) -> billmgr.db.Record:
    """Find a BTG Pix payment record by its BILLmanager payment ID.

    Args:
        elid: The payment ID (numeric string, cast to int for safety).

    Returns:
        Database record with columns: id, externalid, status, xmlparams.

    Raises:
        Exception: If no matching record is found (via ``get_first_record_unwrap``).
    """
    safe_id = int(elid)
    return billmgr.db.get_first_record_unwrap(
        f"SELECT pay.id, pay.externalid, pay.status, pm.xmlparams,"
        f" pm.id AS paymethod_id"
        f" FROM payment AS pay"
        f" JOIN paymethod AS pm ON pay.paymethod = pm.id"
        f" WHERE pm.module = \"{MODULE_NAME}\""
        f"   AND pay.id = \"{safe_id}\""
    )


def find_payment_by_collection_id(collection_id: str) -> Optional[billmgr.db.Record]:
    """Find a BTG Pix payment record by its BTG collection ID (externalid).

    Args:
        collection_id: The BTG collection UUID stored as ``payment.externalid``.

    Returns:
        Database record or ``None`` if not found.

    Raises:
        ValueError: If ``collection_id`` is not a valid UUID format.
    """
    if not _UUID_RE.match(collection_id):
        raise ValueError(f"Invalid collection ID format: {collection_id!r}")

    return billmgr.db.get_first_record(
        f"SELECT pay.id, pay.externalid, pay.status, pm.xmlparams,"
        f" pm.id AS paymethod_id"
        f" FROM payment AS pay"
        f" JOIN paymethod AS pm ON pay.paymethod = pm.id"
        f" WHERE pm.module = \"{MODULE_NAME}\""
        f"   AND pay.externalid = \"{collection_id}\""
    )


def escape_html(text: str) -> str:
    """Escape a string for safe insertion into HTML attributes and content.

    Prevents XSS by encoding ``<``, ``>``, ``&``, ``"``, and ``'``.

    Args:
        text: Raw string that may contain HTML-special characters.

    Returns:
        Escaped string safe for HTML output.
    """
    return _html_module.escape(text, quote=True)
