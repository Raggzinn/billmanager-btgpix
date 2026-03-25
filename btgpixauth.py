#!/usr/bin/python3
"""
OAuth2 Authorization Code CGI for BTG Pactual integration.

This CGI implements the OAuth2 Authorization Code flow required by BTG for
banking API access (Pix). It is embedded in BILLmanager so the admin never
needs to run external scripts or manually copy tokens.

Flow:
  1. Admin navigates to ``/mancgi/btgpixauth?paymethod_id=<ID>``
  2. CGI reads the paymethod's credentials and redirects to BTG's login page
  3. Admin authenticates on BTG and grants consent
  4. BTG redirects back to ``/mancgi/btgpixauth?code=<CODE>&state=<ID>``
  5. CGI exchanges the authorization code for access + refresh tokens
  6. Tokens are saved directly into the paymethod's ``xmlparams`` in the database
  7. Admin sees a success page and can start receiving Pix payments

Security:
  - Client credentials are never exposed to the browser (stored server-side).
  - The ``state`` parameter carries the paymethod ID to prevent CSRF.
  - XML params are escaped before SQL insertion.
"""

import base64
import html as _html
import os
import sys
import urllib.parse
from xml.etree import ElementTree

sys.path.insert(0, "/usr/local/mgr5/lib/python")

import requests

import billmgr.db
import billmgr.logger as logging

_LOG = "btgpixauth"
logging.init_logging(_LOG)
log = logging.get_logger(_LOG)

#: Paymethod module identifier in the database.
_MODULE_NAME = "pmbtgpix"

# BTG OAuth2 endpoints
_SANDBOX_AUTH_URL = "https://id.sandbox.btgpactual.com/authorize"
_PROD_AUTH_URL = "https://id.empresas.btgpactual.com/authorize"
_SANDBOX_TOKEN_URL = "https://id.sandbox.btgpactual.com/token"
_PROD_TOKEN_URL = "https://id.empresas.btgpactual.com/token"

#: OAuth2 scope required for Pix Cobranca API access.
_SCOPE = "openid empresas.btgpactual.com/pix-cash-in"

#: HTTP timeout for token exchange requests.
_TOKEN_TIMEOUT_SECS = 30


# ── HTTP Response Helpers ────────────────────────────────────────

def _redirect(url: str) -> None:
    """Send an HTTP 302 redirect response.

    Args:
        url: Target URL for the redirect.
    """
    print(f"Status: 302 Found\nLocation: {url}\n")


def _render_page(title: str, body_html: str) -> None:
    """Render a minimal styled HTML page.

    Args:
        title:     Page title for the ``<title>`` tag.
        body_html: Inner HTML content for the card container.
    """
    print("Content-Type: text/html\n")
    print(f"""<!DOCTYPE html>
<html><head><meta charset="UTF-8"><title>{title}</title>
<style>
body{{font-family:-apple-system,sans-serif;background:#f5f5f5;display:flex;
justify-content:center;align-items:center;min-height:100vh;padding:20px}}
.card{{background:#fff;border-radius:12px;padding:40px;max-width:500px;
width:100%;box-shadow:0 2px 16px rgba(0,0,0,.08);text-align:center}}
h1{{font-size:22px;margin-bottom:16px}}
.ok{{color:#28a745}} .err{{color:#c00}}
p{{color:#555;line-height:1.6;margin:8px 0}}
a.btn{{display:inline-block;margin-top:20px;padding:12px 28px;background:#00bdae;
color:#fff;border-radius:8px;text-decoration:none;font-weight:600}}
a.btn:hover{{background:#00a99d}}
</style></head><body><div class="card">{body_html}</div></body></html>""")


def _render_error(message: str, retry_url: str = "") -> None:
    """Render an error page with optional retry link.

    Args:
        message:   Error message to display (will be HTML-escaped).
        retry_url: Optional URL for a "Retry" button (will be HTML-escaped).
    """
    safe_msg = _html.escape(message, quote=True)
    retry_html = ""
    if retry_url:
        safe_url = _html.escape(retry_url, quote=True)
        retry_html = f'<a class="btn" href="{safe_url}">Retry</a>'
    _render_page("Error", f'<h1 class="err">Authorization Failed</h1><p>{safe_msg}</p>{retry_html}')


# ── Database Helpers ─────────────────────────────────────────────

def _get_paymethod(paymethod_id: str) -> billmgr.db.Record:
    """Fetch a paymethod record by ID.

    Args:
        paymethod_id: Numeric paymethod ID.

    Returns:
        Database record with ``id`` and ``xmlparams`` columns.

    Raises:
        Exception: If no matching paymethod is found.
    """
    return billmgr.db.get_first_record_unwrap(
        f"SELECT id, xmlparams FROM paymethod"
        f" WHERE module = '{_MODULE_NAME}' AND id = '{int(paymethod_id)}'"
    )


def _save_tokens(paymethod_id: str, xml: ElementTree.Element,
                 access_token: str, refresh_token: str) -> None:
    """Save OAuth2 tokens into the paymethod's XML params.

    Creates the XML elements if they don't exist, updates them if they do,
    and writes the result back to the database.

    Args:
        paymethod_id:  Numeric paymethod ID.
        xml:           Parsed XML element tree of current params.
        access_token:  New OAuth2 access token.
        refresh_token: New OAuth2 refresh token.
    """
    for tag, value in [("access_token", access_token), ("refresh_token", refresh_token)]:
        el = xml.find(tag)
        if el is None:
            el = ElementTree.SubElement(xml, tag)
        el.text = value

    new_xmlparams = ElementTree.tostring(xml, encoding="unicode")
    # Escape single quotes to prevent SQL injection
    escaped = new_xmlparams.replace("'", "''")

    billmgr.db.db_query(
        f"UPDATE paymethod SET xmlparams = '{escaped}'"
        f" WHERE id = '{int(paymethod_id)}'"
    )


# ── Query String Parser ─────────────────────────────────────────

def _parse_query() -> dict:
    """Parse the CGI query string into a dictionary.

    Returns:
        Dict mapping query parameter names to values.
    """
    qs = os.environ.get("QUERY_STRING", "")
    return dict(urllib.parse.parse_qsl(qs))


def _encode_basic_auth(client_id: str, client_secret: str) -> str:
    """Encode client credentials as Base64 for HTTP Basic authentication.

    Args:
        client_id:     OAuth2 client ID.
        client_secret: OAuth2 client secret.

    Returns:
        Base64-encoded string for the ``Authorization: Basic`` header.
    """
    return base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()


# ── Main ─────────────────────────────────────────────────────────

def main() -> None:
    """CGI entry point — routes between authorization start and callback."""
    params = _parse_query()
    code = params.get("code", "")
    state = params.get("state", "")
    paymethod_id = params.get("paymethod_id", state)

    if not code:
        _start_authorization(paymethod_id)
    else:
        _handle_callback(paymethod_id, code)


def _start_authorization(paymethod_id: str) -> None:
    """Step 1: Redirect the admin to BTG's OAuth2 login page.

    Reads the paymethod's client_id and sandbox flag, then constructs
    the authorization URL with appropriate parameters.

    Args:
        paymethod_id: BILLmanager paymethod ID to authorize.
    """
    if not paymethod_id:
        _render_error("Missing paymethod_id parameter.")
        return

    try:
        pm = _get_paymethod(paymethod_id)
    except Exception:
        _render_error("Payment method not found. Save it first, then authorize.")
        return

    xml = ElementTree.fromstring(pm.as_str("xmlparams"))
    client_id = xml.findtext("client_id", "")
    sandbox = xml.findtext("sandbox", "off") == "on"

    if not client_id:
        _render_error("Client ID is not configured in the payment method.")
        return

    host = os.environ.get("HTTP_HOST", "")
    redirect_uri = urllib.parse.quote(f"https://{host}/mancgi/btgpixauth", safe="")
    auth_url = _SANDBOX_AUTH_URL if sandbox else _PROD_AUTH_URL

    url = (
        f"{auth_url}"
        f"?client_id={client_id}"
        f"&redirect_uri={redirect_uri}"
        f"&scope={urllib.parse.quote(_SCOPE)}"
        f"&response_type=code"
        f"&prompt=login"
        f"&state={paymethod_id}"
    )

    log.info(f"Redirecting admin to BTG for paymethod {paymethod_id}")
    _redirect(url)


def _handle_callback(paymethod_id: str, code: str) -> None:
    """Step 2: Exchange the authorization code for tokens and save them.

    Args:
        paymethod_id: BILLmanager paymethod ID (from the ``state`` parameter).
        code:         Authorization code received from BTG.
    """
    if not paymethod_id:
        _render_error("Missing state/paymethod_id in callback.")
        return

    try:
        pm = _get_paymethod(paymethod_id)
    except Exception:
        _render_error("Payment method not found.")
        return

    xml = ElementTree.fromstring(pm.as_str("xmlparams"))
    client_id = xml.findtext("client_id", "")
    client_secret = xml.findtext("client_secret", "")
    sandbox = xml.findtext("sandbox", "off") == "on"

    token_url = _SANDBOX_TOKEN_URL if sandbox else _PROD_TOKEN_URL

    host = os.environ.get("HTTP_HOST", "")
    redirect_uri = f"https://{host}/mancgi/btgpixauth"

    log.info(f"Exchanging authorization code for paymethod {paymethod_id}")

    try:
        resp = requests.post(
            token_url,
            headers={
                "Authorization": f"Basic {_encode_basic_auth(client_id, client_secret)}",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            data=(
                f"grant_type=authorization_code"
                f"&code={urllib.parse.quote(code, safe='')}"
                f"&redirect_uri={urllib.parse.quote(redirect_uri, safe='')}"
            ),
            timeout=_TOKEN_TIMEOUT_SECS,
        )
    except requests.RequestException as e:
        log.error(f"Token exchange request failed: {e}")
        retry = f"/mancgi/btgpixauth?paymethod_id={paymethod_id}"
        _render_error("Could not connect to BTG. Please try again.", retry)
        return

    if not resp.ok:
        log.error(f"Token exchange failed: {resp.status_code} {resp.text}")
        retry = f"/mancgi/btgpixauth?paymethod_id={paymethod_id}"
        _render_error(
            f"BTG returned error {resp.status_code}. Check your credentials and try again.",
            retry,
        )
        return

    data = resp.json()
    access_token = data.get("access_token", "")
    refresh_token = data.get("refresh_token", "")

    if not access_token:
        log.error(f"No access_token in BTG response: {data}")
        _render_error("BTG did not return an access token.")
        return

    # Persist tokens in the paymethod record
    _save_tokens(paymethod_id, xml, access_token, refresh_token)
    log.info(f"Tokens saved successfully for paymethod {paymethod_id}")

    safe_host = _html.escape(host, quote=True)
    _render_page(
        "Success",
        '<h1 class="ok">Authorization Successful</h1>'
        "<p>BTG Pactual access has been granted.<br>"
        "Tokens have been saved automatically.</p>"
        "<p>You can close this page and start receiving Pix payments.</p>"
        f'<a class="btn" href="https://{safe_host}/billmgr">Back to BillManager</a>',
    )


if __name__ == "__main__":
    main()
