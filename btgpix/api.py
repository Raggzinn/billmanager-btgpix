"""
BTG Pactual Empresas Pix API client.

Handles OAuth2 token refresh and HTTP requests to the BTG Pix Cobranca API.
All banking API calls require tokens obtained via the Authorization Code flow,
which is handled by the ``btgpixauth`` CGI. This client only deals with
token refresh and API consumption.

Reference:
    - Auth: https://developers.empresas.btgpactual.com/docs/client-credentials
    - Pix:  https://developers.empresas.btgpactual.com/docs/pix-cobranca
    - API:  https://developers.empresas.btgpactual.com/reference/post_companies-companyid-pix-cash-in-instant-collections
"""

import base64
import urllib.parse
from typing import Optional, Dict, Any, Callable

import requests

import billmgr.logger as logging

from btgpix.exceptions import BTGApiError, BTGAuthError, BTGResponseError

_LOG = "btgpix"

# BTG Pactual OAuth2 token endpoints
_SANDBOX_TOKEN_URL = "https://id.sandbox.btgpactual.com/token"
_PROD_TOKEN_URL = "https://id.empresas.btgpactual.com/token"

# BTG Pactual API base URLs
_SANDBOX_API_URL = "https://api.sandbox.empresas.btgpactual.com/v1"
_PROD_API_URL = "https://api.empresas.btgpactual.com/v1"

#: Default collection expiration in seconds (24 hours).
DEFAULT_EXPIRATION_SECS = 86400

#: HTTP request timeout in seconds.
REQUEST_TIMEOUT_SECS = 30

#: Maximum length for the displayText field per BTG API spec.
MAX_DISPLAY_TEXT_LENGTH = 140


class BTGPixAPI:
    """HTTP client for the BTG Pactual Empresas Pix Cobranca API.

    This client uses OAuth2 Bearer tokens for authentication and automatically
    attempts a token refresh on 401 responses.

    Args:
        client_id:     OAuth2 application client ID.
        client_secret: OAuth2 application client secret.
        company_id:    Company CNPJ (numbers only) used as path parameter.
        pix_key:       Registered Pix key for receiving payments.
        access_token:  Current OAuth2 access token (may be expired).
        refresh_token: OAuth2 refresh token for obtaining new access tokens.
        sandbox:       If True, use sandbox URLs instead of production.
        on_token_refresh: Optional callback invoked after a successful token
            refresh, receiving ``(new_access_token, new_refresh_token)``.
            Use this to persist tokens back to the database.
    """

    def __init__(
        self,
        client_id: str,
        client_secret: str,
        company_id: str,
        pix_key: str,
        access_token: str = "",
        refresh_token: str = "",
        sandbox: bool = False,
        on_token_refresh: Optional[Callable[[str, str], None]] = None,
    ) -> None:
        self._client_id = client_id
        self._client_secret = client_secret
        self._company_id = company_id
        self._pix_key = pix_key
        self._access_token = access_token
        self._refresh_token = refresh_token
        self._on_token_refresh = on_token_refresh

        self._token_url = _SANDBOX_TOKEN_URL if sandbox else _PROD_TOKEN_URL
        self._api_url = _SANDBOX_API_URL if sandbox else _PROD_API_URL

    # ── Authentication ────────────────────────────────────────────

    def refresh_access_token(self) -> Dict[str, Any]:
        """Obtain a new access token using the stored refresh token.

        Updates internal ``_access_token`` and ``_refresh_token`` on success.

        Returns:
            Full token response dict from BTG (access_token, refresh_token, etc.).

        Raises:
            BTGAuthError: If no refresh token is available or the request fails.
        """
        log = logging.get_logger(_LOG)

        if not self._refresh_token:
            raise BTGAuthError("No refresh token available — re-authorize via btgpixauth")

        log.info("Refreshing access token")

        resp = requests.post(
            self._token_url,
            headers={
                "Authorization": f"Basic {self._encode_basic_auth()}",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            data=f"grant_type=refresh_token&refresh_token={urllib.parse.quote(self._refresh_token, safe='')}",
            timeout=REQUEST_TIMEOUT_SECS,
        )

        if not resp.ok:
            raise BTGAuthError(
                f"Token refresh failed ({resp.status_code}): {resp.text}"
            )

        data = resp.json()
        self._access_token = data["access_token"]
        self._refresh_token = data.get("refresh_token", self._refresh_token)
        log.info("Access token refreshed successfully")

        if self._on_token_refresh:
            self._on_token_refresh(self._access_token, self._refresh_token)

        return data

    @property
    def access_token(self) -> str:
        """Current OAuth2 access token."""
        return self._access_token

    @property
    def refresh_token(self) -> str:
        """Current OAuth2 refresh token."""
        return self._refresh_token

    # ── Pix Collections ──────────────────────────────────────────

    def create_collection(
        self,
        amount: float,
        expires_in: int = DEFAULT_EXPIRATION_SECS,
        display_text: str = "",
        payer_name: str = "",
        payer_tax_id: str = "",
        tags: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Create a Pix instant collection (charge with QR code).

        Args:
            amount:       Payment amount in BRL (e.g. 49.90).
            expires_in:   Time-to-live in seconds before the QR code expires.
            display_text: Optional message shown to the payer (max 140 chars).
            payer_name:   Optional payer name.
            payer_tax_id: Optional payer CPF or CNPJ.
            tags:         Optional key-value pairs for webhook identification.

        Returns:
            BTG response dict containing at minimum:
              - ``id``: Collection UUID
              - ``txId``: Transaction identifier
              - ``status``: Initial status (usually "ACTIVE")
              - ``emv``: Pix copy-and-paste code (EMV payload)
              - ``location.url``: URL of the QR code image hosted by BTG

        Raises:
            BTGResponseError: If the response is missing the ``id`` field.
            BTGApiError: On any HTTP error from BTG.
        """
        payload: Dict[str, Any] = {
            "pixKey": self._pix_key,
            "expiresIn": expires_in,
            "amount": {"original": amount},
        }

        if display_text:
            payload["displayText"] = display_text[:MAX_DISPLAY_TEXT_LENGTH]

        if payer_name or payer_tax_id:
            payer: Dict[str, str] = {}
            if payer_name:
                payer["name"] = payer_name
            if payer_tax_id:
                payer["taxId"] = payer_tax_id
            payload["payer"] = payer

        if tags:
            payload["tags"] = tags

        result = self._request(
            "POST",
            f"/companies/{self._company_id}/pix-cash-in/instant-collections",
            json_data=payload,
        )

        if "id" not in result:
            raise BTGResponseError(f"Collection response missing 'id' field: {result}")

        return result

    def get_collection(self, collection_id: str) -> Dict[str, Any]:
        """Retrieve a single collection by its UUID.

        Uses the banking collections listing endpoint filtered by ID.

        Args:
            collection_id: The collection UUID returned by :meth:`create_collection`.

        Returns:
            Collection dict with status, amount, payer, detail, etc.

        Raises:
            BTGResponseError: If no collection matches the given ID.
        """
        result = self._request(
            "GET",
            f"/{self._company_id}/banking/collections",
            params={"id": collection_id, "pageSize": 1, "pageNumber": 1},
        )
        data = result.get("data", [])
        if not data:
            raise BTGResponseError(f"Collection {collection_id} not found")
        return data[0]

    def get_collection_status(self, collection_id: str) -> str:
        """Get just the status string of a collection.

        Convenience wrapper around :meth:`get_collection`.

        Args:
            collection_id: The collection UUID.

        Returns:
            Status string (e.g. "ACTIVE", "PAID", "CANCELED").
        """
        return self.get_collection(collection_id).get("status", "")

    # ── Internal ─────────────────────────────────────────────────

    def _request(
        self,
        method: str,
        path: str,
        json_data: Optional[Dict] = None,
        params: Optional[Dict] = None,
    ) -> Dict[str, Any]:
        """Execute an authenticated API request with automatic token refresh.

        On a 401 response, attempts to refresh the access token and retries
        the request exactly once.

        Args:
            method:    HTTP method (GET, POST, etc.).
            path:      API path appended to the base URL.
            json_data: Optional JSON body for POST/PUT requests.
            params:    Optional query string parameters.

        Returns:
            Parsed JSON response as a dict.

        Raises:
            BTGApiError: On any non-2xx response (after retry if applicable).
        """
        log = logging.get_logger(_LOG)
        url = f"{self._api_url}{path}"

        resp = self._do_request(method, url, json_data, params)

        # Automatic retry on expired token
        if resp.status_code == 401 and self._refresh_token:
            log.info(f"Got 401 on {method} {path}, refreshing token and retrying")
            self.refresh_access_token()
            resp = self._do_request(method, url, json_data, params)

        if not resp.ok:
            raise BTGApiError(
                f"{method} {url} returned {resp.status_code}: {resp.text}"
            )

        try:
            return resp.json()
        except (ValueError, KeyError) as e:
            raise BTGResponseError(
                f"{method} {url} returned invalid JSON: {e}"
            ) from e

    def _do_request(
        self,
        method: str,
        url: str,
        json_data: Optional[Dict],
        params: Optional[Dict],
    ) -> requests.Response:
        """Execute a single HTTP request (no retry logic)."""
        return requests.request(
            method,
            url,
            headers={
                "Authorization": f"Bearer {self._access_token}",
                "Content-Type": "application/json",
            },
            json=json_data,
            params=params,
            timeout=REQUEST_TIMEOUT_SECS,
        )

    def _encode_basic_auth(self) -> str:
        """Encode client credentials as Base64 for HTTP Basic auth."""
        credentials = f"{self._client_id}:{self._client_secret}"
        return base64.b64encode(credentials.encode()).decode()
