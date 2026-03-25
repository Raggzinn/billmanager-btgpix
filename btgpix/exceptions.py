"""Exception hierarchy for BTG Pactual API errors.

All exceptions auto-log the error message on creation.

Hierarchy:
    BTGApiError (base)
    ├── BTGAuthError      — OAuth2 token or credential failures
    └── BTGResponseError  — Unexpected or malformed API responses
"""

import billmgr.logger as logging

_LOG = "btgpix"


class BTGApiError(Exception):
    """Base exception for all BTG API errors.

    Automatically logs the error message at ERROR level.
    """

    def __init__(self, msg: str) -> None:
        super().__init__(msg)
        logging.get_logger(_LOG).error(msg)


class BTGAuthError(BTGApiError):
    """Raised when authentication or token refresh fails."""


class BTGResponseError(BTGApiError):
    """Raised when the API returns an unexpected or invalid response."""
