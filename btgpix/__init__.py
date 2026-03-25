"""
BTG Pactual Pix integration library for BILLmanager 6.

Provides:
  - BTGPixAPI: HTTP client for BTG Pactual Empresas Pix Cobranca API
  - CollectionStatus: enum of possible collection statuses
  - Exception hierarchy: BTGApiError > BTGAuthError, BTGResponseError
"""
import sys

if "/usr/local/mgr5/lib/python" not in sys.path:
    sys.path.insert(0, "/usr/local/mgr5/lib/python")
