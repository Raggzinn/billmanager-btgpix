#!/usr/bin/python3
"""
Payment CGI — customer-facing endpoint for Pix payments.

When a customer clicks "Pay" in BILLmanager, this CGI:
  1. Creates a Pix collection on BTG via the API.
  2. Renders an HTML page with the QR code and copy-and-paste code.
  3. Polls for payment confirmation via JavaScript and redirects on success.

If the payment is already in progress, it re-checks the collection status
and either shows the existing QR code or redirects to the appropriate page.
"""

import os
import sys
import urllib.parse

sys.path.insert(0, "/usr/local/mgr5/lib/python")

import billmgr.logger as logging
from billmgr import payment
from billmgr.modules.paymentcgi import PageType, PaymentCgi, PaymentCgiType, run_cgi

from btgpix.enums import CollectionStatus
from btgpix.utils import api_from_dict, escape_html

_LOG = "btgpixpayment"
logging.init_logging(_LOG)
log = logging.get_logger(_LOG)

#: Collection expiration in seconds (1 hour).
COLLECTION_EXPIRATION_SECS = 3600

#: Frontend polling interval in milliseconds.
POLL_INTERVAL_MS = 10_000

#: QR code image size in pixels.
QR_SIZE = 240


# ── HTML Template ────────────────────────────────────────────────
#
# Uses double-brace {{ }} for literal braces in the CSS/JS,
# and single-brace {var} for Python string formatting.
#

_QR_PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Pix Payment</title>
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;
background:#f5f5f5;display:flex;justify-content:center;align-items:center;
min-height:100vh;padding:20px}}
.card{{background:#fff;border-radius:16px;padding:40px;max-width:460px;width:100%;
box-shadow:0 4px 24px rgba(0,0,0,.08);text-align:center}}
h1{{font-size:22px;color:#333;margin-bottom:6px}}
.amount{{font-size:30px;font-weight:700;color:#00bdae;margin:12px 0 24px}}
.qr{{border:2px solid #e8e8e8;border-radius:12px;padding:16px;display:inline-block;
margin-bottom:24px;background:#fff}}
.qr img{{width:{qr_size}px;height:{qr_size}px;display:block}}
.copy label{{display:block;font-size:13px;color:#888;margin-bottom:6px}}
.copy-row{{display:flex;gap:8px;margin-bottom:20px}}
.copy-row input{{flex:1;padding:10px 12px;border:1px solid #ddd;border-radius:8px;
font-size:12px;background:#fafafa;color:#333}}
.btn{{padding:10px 20px;border:none;border-radius:8px;cursor:pointer;font-weight:600;
font-size:14px;transition:background .2s}}
.btn-copy{{background:#00bdae;color:#fff}}
.btn-copy:hover{{background:#00a99d}}
.btn-copy.ok{{background:#4caf50}}
.btn-check{{display:inline-block;margin-top:12px;padding:12px 32px;background:#32b768;
color:#fff;font-size:15px;text-decoration:none;border-radius:8px}}
.btn-check:hover{{background:#28a745}}
.info{{font-size:13px;color:#999;line-height:1.6}}
.expire{{font-size:12px;color:#ff9800;margin-top:10px}}
.status{{margin-top:16px;padding:10px;border-radius:8px;font-size:14px;display:none}}
.status.checking{{display:block;background:#fff3cd;color:#856404}}
.status.paid{{display:block;background:#d4edda;color:#155724}}
</style>
</head>
<body>
<div class="card">
  <h1>Pix Payment</h1>
  <div class="amount">R$ {amount}</div>

  {qr_block}

  <div class="copy">
    <label>Pix copy-and-paste code:</label>
    <div class="copy-row">
      <input type="text" id="pix" value="{emv_escaped}" readonly>
      <button class="btn btn-copy" onclick="copyPix(this)">Copy</button>
    </div>
  </div>

  <p class="info">
    Open your banking app, choose Pay with Pix,<br>
    and scan the QR code or paste the code above.
  </p>
  <p class="expire">Expires in {expire_min} minutes</p>

  <div id="st" class="status"></div>
  <a href="{result_url}" class="btn-check">I already paid</a>
</div>

<script>
function copyPix(b) {{
  navigator.clipboard.writeText(document.getElementById('pix').value);
  b.textContent = 'Copied!';
  b.classList.add('ok');
  setTimeout(function() {{ b.textContent = 'Copy'; b.classList.remove('ok'); }}, 2000);
}}

// Auto-poll for payment confirmation
(function poll() {{
  setTimeout(function() {{
    fetch('{check_url}')
      .then(function(r) {{ return r.json(); }})
      .then(function(d) {{
        var s = document.getElementById('st');
        if (d.paid) {{
          s.className = 'status paid';
          s.textContent = 'Payment confirmed! Redirecting...';
          setTimeout(function() {{ window.location.href = '{success_url}'; }}, 1500);
        }} else {{
          s.className = 'status checking';
          s.textContent = 'Waiting for payment...';
          poll();
        }}
      }})
      .catch(poll);
  }}, {poll_interval});
}})();
</script>
</body>
</html>"""


class BTGPixPaymentCgi(PaymentCgi):
    """CGI handler for creating Pix collections and rendering the QR code page."""

    def cgi_type(self) -> PaymentCgiType:
        return PaymentCgiType.Payment

    def process(self) -> None:
        """Main entry point called by BILLmanager's CGI framework."""
        log.info("Payment CGI invoked")
        api = api_from_dict(self.paymethod_params)

        # If the payment is already in progress, re-check its status
        if int(self.payment_params["status"]) == int(payment.PaymentStatus.psInPay.value):
            collection_id = self.payment_params.get("externalid", "")
            if collection_id and self._handle_existing(api, collection_id):
                return

        # Create a new Pix collection
        self._create_new_collection(api)

    def _handle_existing(self, api, collection_id: str) -> bool:
        """Re-check an existing in-progress collection.

        Args:
            api: Authenticated BTG API client.
            collection_id: BTG collection UUID.

        Returns:
            True if the request was handled (redirect or render), False to
            fall through and create a new collection.
        """
        try:
            cob = api.get_collection(collection_id)
            status = cob.get("status", "")

            if status == CollectionStatus.PAID:
                payment.set_paid(
                    int(self.elid()), info="pix_paid", external_id=collection_id
                )
                self.redirect_to_url(self.get_page(PageType.Success))
                return True

            if status in (CollectionStatus.ACTIVE, CollectionStatus.CREATED):
                self._render_qr_page(cob)
                return True

        except Exception as e:
            log.error(f"Error re-checking collection {collection_id}: {e}")

        return False

    def _create_new_collection(self, api) -> None:
        """Create a new Pix collection and render the QR code page.

        Args:
            api: Authenticated BTG API client.
        """
        try:
            amount = float(self.payment_params["paymethodamount"])
            order = self.payment_params["number"]
            desc = self.payment_params.get("description", f"Invoice {order}")

            cob = api.create_collection(
                amount=amount,
                display_text=f"Invoice {order} - {desc}"[:140],
                expires_in=COLLECTION_EXPIRATION_SECS,
            )

            collection_id = cob["id"]
            payment.set_in_pay(
                int(self.elid()), info="", external_id=collection_id
            )
            log.info(f"Collection created: id={collection_id}")

            self._render_qr_page(cob)

        except Exception as e:
            log.error(f"Error creating Pix collection: {e}")
            self.redirect_to_url(self.get_page(PageType.Fail))

    def _render_qr_page(self, cob: dict) -> None:
        """Render the HTML page with QR code, copy-paste code, and auto-polling.

        Prefers the QR code image URL from BTG (``location.url``). Falls back
        to generating one via a public QR code API using the EMV payload.

        Args:
            cob: Collection response dict from BTG API.
        """
        emv = cob.get("emv", "")
        location = cob.get("location", {})
        qr_image_url = location.get("url", "")
        amount_raw = cob.get("amount", {}).get(
            "value", cob.get("amount", {}).get("original", "0")
        )
        expire_min = COLLECTION_EXPIRATION_SECS // 60

        host = os.environ.get("HTTP_HOST", "")
        webhook_base = f"https://{host}/mancgi/btgpixwebhook"
        result_url = f"{webhook_base}?elid={self.elid()}"
        check_url = f"{webhook_base}?elid={self.elid()}&check=1"
        success_url = self.get_page(PageType.Success)

        # Build QR code block: prefer BTG-hosted image, fallback to public API
        if qr_image_url:
            qr_block = (
                f'<div class="qr">'
                f'<img src="{escape_html(qr_image_url)}" alt="QR Code Pix">'
                f'</div>'
            )
        elif emv:
            encoded_emv = urllib.parse.quote(emv)
            qr_block = (
                f'<div class="qr">'
                f'<img src="https://api.qrserver.com/v1/create-qr-code/'
                f'?size={QR_SIZE}x{QR_SIZE}&data={encoded_emv}" '
                f'alt="QR Code Pix">'
                f'</div>'
            )
        else:
            qr_block = (
                '<p class="info" style="color:#c00">'
                'QR code unavailable. Use the code below.'
                '</p>'
            )

        html = _QR_PAGE.format(
            amount=f"{float(amount_raw):.2f}",
            qr_block=qr_block,
            emv_escaped=escape_html(emv),
            expire_min=expire_min,
            result_url=escape_html(result_url),
            check_url=escape_html(check_url),
            success_url=escape_html(success_url),
            poll_interval=POLL_INTERVAL_MS,
            qr_size=QR_SIZE,
        )

        print("Content-Type: text/html\n")
        print(html)


if __name__ == "__main__":
    run_cgi(BTGPixPaymentCgi)
