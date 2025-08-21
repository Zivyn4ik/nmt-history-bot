from __future__ import annotations

import time
import uuid
import hmac
import hashlib
import logging
from decimal import Decimal, ROUND_HALF_UP
from typing import Dict, Any, Optional

import httpx

from ..config import settings
from ..services import activate_or_extend

log = logging.getLogger("bot.payments")
WFP_API = "https://api.wayforpay.com/api"

# ---------- helpers ----------
def money2(x: float | int | str) -> str:
    """Ровно 2 знака после точки в виде строки."""
    return str(Decimal(str(x)).quantize(Decimal("0.00"), rounding=ROUND_HALF_UP))

def hmac_md5_hex(message: str, secret: str) -> str:
    """Подпись WayForPay — HMAC-MD5(message, key=secret)."""
    return hmac.new(secret.strip().encode("utf-8"),
                    message.strip().encode("utf-8"),
                    hashlib.md5).hexdigest()

def make_base(
    merchant: str,
    domain: str,
    order_ref: str,
    order_date: int,
    amount_str: str,
    currency: str,
    product_name: str,
    product_count: int = 1,
    product_price_str: Optional[str] = None,
) -> str:
    if product_price_str is None:
        product_price_str = amount_str
    return (
        f"{merchant};{domain};{order_ref};{order_date};"
        f"{amount_str};{currency};{product_name};{product_count};{product_price_str}"
    )

# ---------- public API ----------
async def create_invoice(
    user_id: int,
    amount: float,
    currency: str = "UAH",
    product_name: str = "Access to course (1 month)",
) -> str:
    order_date = int(time.time())
    order_ref = f"sub-{user_id}-{order_date}-{uuid.uuid4().hex[:6]}"

    merchant = settings.WFP_MERCHANT.strip()
    domain = settings.WFP_DOMAIN.strip()
    secret = settings.WFP_SECRET.strip()

    amt = money2(amount)
    base = make_base(merchant, domain, order_ref, order_date, amt, currency, product_name, 1, amt)
    signature = hmac_md5_hex(base, secret)

    # ВАЖНО: returnUrl теперь на ваш домен → /wfp/return, который делает 303 на t.me
    return_url = settings.BASE_URL.rstrip("/") + "/wfp/return"
    service_url = settings.BASE_URL.rstrip("/") + "/payments/wayforpay/callback"

    payload = {
        "transactionType": "CREATE_INVOICE",
        "merchantAccount": merchant,
        "merchantDomainName": domain,
        "apiVersion": 1,
        "orderReference": order_ref,
        "orderDate": order_date,
        "amount": amt,               # строка 'NNN.MM'
        "currency": currency,
        "productName": [product_name],
        "productPrice": [amt],       # строка 'NNN.MM'
        "productCount": [1],
        "returnUrl": return_url,     # <= теперь наш эндпоинт
        "serviceUrl": service_url,
        "merchantSignature": signature,
    }

    print("📤 WFP payload:", {k: v for k, v in payload.items() if k != "merchantSignature"})
    print("🔧 base =", base)
    print("🔑 signature =", signature)

    async with httpx.AsyncClient(timeout=25) as cli:
        r = await cli.post(WFP_API, json=payload)
        r.raise_for_status()
        data = r.json()
        print("📥 WFP response:", data)

    url = data.get("invoiceUrl") or data.get("formUrl") or data.get("url")
    if not url:
        raise RuntimeError(f"WayForPay error: {data.get('reasonCode')} — {data.get('reason')}")
    return url

def verify_callback_signature(_data: Dict[str, Any]) -> bool:
    # можно добавить проверку, если понадобится
    return True

async def process_callback(bot, data: Dict[str, Any]) -> None:
    try:
        if not verify_callback_signature(data):
            print("⚠️ Callback signature failed:", data)
            return

        status = (data.get("transactionStatus") or data.get("status") or "").lower()
        order_ref = data.get("orderReference", "")
        print("✅ WFP callback received:", status, order_ref)

        if status in ("approved", "accept", "success") and order_ref.startswith("sub-"):
            try:
                user_id = int(order_ref.split("-")[1])
            except Exception:
                print("🚫 Cannot parse user_id from order_ref:", order_ref)
                return
            await activate_or_extend(bot, user_id)
    except Exception:
        log.exception("Unhandled error in WFP callback handler")
