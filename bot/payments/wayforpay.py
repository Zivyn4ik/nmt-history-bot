
from __future__ import annotations
import time, uuid, hashlib, logging
import httpx
from typing import Dict, Any
from ..config import settings
from ..services import activate_or_extend

log = logging.getLogger("bot.payments")
WFP_API = "https://api.wayforpay.com/api"

def md5_hex(s: str) -> str:
    return hashlib.md5(s.encode("utf-8")).hexdigest()

def sign_create_invoice(
    merchant: str,
    domain: str,
    order_ref: str,
    order_date: int,
    amount: float,
    currency: str,
    product_name: str,
    secret: str,
) -> str:
    # MD5( merchantAccount;merchantDomainName;orderReference;orderDate;
    #      amount;currency;productName;productCount;productPrice;merchantSecretKey )
    parts = [
        merchant,
        domain,
        order_ref,
        str(order_date),
        f"{amount:.2f}",
        currency,
        product_name,
        "1",
        f"{amount:.2f}",
        secret,
    ]
    base = ";".join(parts)
    return md5_hex(base)

async def create_invoice(
    user_id: int,
    amount: float,
    currency: str = "UAH",
    product_name: str = "Channel subscription (1 month)",
) -> str:
    order_date = int(time.time())
    order_ref = f"sub-{user_id}-{order_date}-{uuid.uuid4().hex[:6]}"

    merchant = settings.WFP_MERCHANT
    domain = settings.WFP_DOMAIN
    secret = settings.WFP_SECRET

    payload = {
        "transactionType": "CREATE_INVOICE",
        "merchantAccount": merchant,
        "merchantDomainName": domain,
        "apiVersion": 1,
        "orderReference": order_ref,
        "orderDate": order_date,
        "amount": round(amount, 2),
        "currency": currency,
        "productName": [product_name],
        "productPrice": [round(amount, 2)],
        "productCount": [1],
        "merchantSignature": sign_create_invoice(
            merchant, domain, order_ref, order_date, amount, currency, product_name, secret
        ),
        "returnUrl": f"https://{settings.BASE_URL}/thanks",
        "serviceUrl": f"https://{settings.BASE_URL}/payments/wayforpay/callback",
    }

    log.info("CREATE_INVOICE %s", {k: v for k, v in payload.items() if k != "merchantSignature"})

    async with httpx.AsyncClient(timeout=25) as cli:
        r = await cli.post(WFP_API, json=payload)
        r.raise_for_status()
        data = r.json()

    log.info("WFP response: %s", data)

    if "invoiceUrl" in data and data["invoiceUrl"]:
        return data["invoiceUrl"]

    reason = data.get("message") or data.get("reason") or data.get("transactionStatus") or data
    raise RuntimeError(f"WayForPay error: {reason}")

def verify_callback_signature(data: Dict[str, Any]) -> bool:
    # TODO: добавить строгую проверку подписи по merchantSecretKey если потребуется
    return True

async def process_callback(bot, data: Dict[str, Any]) -> None:
    if not data:
        return
    if not verify_callback_signature(data):
        log.warning("Callback signature failed: %s", data); return
    status = (data.get("transactionStatus") or data.get("status") or "").lower()
    order_ref = data.get("orderReference", "")
    log.info("WFP callback: status=%s order_ref=%s", status, order_ref)
    if status in ("approved", "accept", "success") and order_ref.startswith("sub-"):
        try:
            user_id = int(order_ref.split("-")[1])
        except Exception:
            log.exception("Cannot parse user_id from order_ref=%s", order_ref); return
        await activate_or_extend(bot, user_id)
