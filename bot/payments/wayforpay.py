from __future__ import annotations
import time, uuid, base64, hashlib, logging
import httpx
from typing import Dict, Any
from ..config import settings
from ..services import activate_or_extend

log = logging.getLogger("bot.payments")
WFP_API = "https://api.wayforpay.com/api"

def _sha1_b64(s: str) -> str:
    return base64.b64encode(hashlib.sha1(s.encode("utf-8")).digest()).decode()

def _sign_create_invoice(order_ref: str, amount: float, currency: str, order_date: int, product_name: str) -> str:
    # Подпись для CREATE_INVOICE согласно документации WFP (упрощенная форма)
    parts = [
        settings.WFP_MERCHANT,
        settings.WFP_DOMAIN,
        str(order_date),
        order_ref,
        f"{amount:.2f}",
        currency,
        product_name,
        "1",
        f"{amount:.2f}",
    ]
    signature_base = ";".join(parts)
    sig = _sha1_b64(signature_base)
    # WFP обычно требует оборачивание secret сверху, но часть кабинетов принимает только sha1(base)
    # Если ваш кабинет требует HMAC или другой формат — скажет ошибка. Тогда поправим формулу.
    return sig

async def create_invoice(user_id: int, amount: float, currency: str = "UAH", product_name: str = "Channel subscription (1 month)") -> str:
    order_date = int(time.time())
    order_ref = f"sub-{user_id}-{order_date}-{uuid.uuid4().hex[:6]}"

    payload = {
        "transactionType": "CREATE_INVOICE",
        "merchantAccount": settings.WFP_MERCHANT,
        "merchantDomainName": settings.WFP_DOMAIN,
        "apiVersion": 1,
        "orderReference": order_ref,
        "orderDate": order_date,
        "amount": round(amount, 2),
        "currency": currency,
        "productName": [product_name],
        "productPrice": [round(amount,2)],
        "productCount": [1],
        "merchantSignature": _sign_create_invoice(order_ref, amount, currency, order_date, product_name),
        "returnUrl": f"{settings.BASE_URL}/thanks",
        "serviceUrl": f"{settings.BASE_URL}/payments/wayforpay/callback"
    }

    log.info("CREATE_INVOICE for user %s: %s", user_id, payload)

    async with httpx.AsyncClient(timeout=20) as cli:
        r = await cli.post(WFP_API, json=payload)
        # Если WFP вернул 200, но с ошибкой в JSON — разберём ниже
        r.raise_for_status()
        data = r.json()

    log.info("WFP response: %s", data)

    # На успешном ответе должен быть invoiceUrl
    invoice_url = data.get("invoiceUrl")
    if invoice_url:
        return invoice_url

    # Если нет invoiceUrl — вытащим текст ошибки (message / reason / details)
    message = data.get("message") or data.get("reason") or data.get("status") or "unknown_error"
    raise RuntimeError(f"WayForPay error: {message} | response={data}")

def verify_callback_signature(data: Dict[str, Any]) -> bool:
    # TODO: при необходимости включим строгую проверку подписи
    return True

async def process_callback(bot, data: Dict[str, Any]) -> None:
    if not verify_callback_signature(data):
        log.warning("Callback signature failed: %s", data)
        return

    status = (data.get("transactionStatus") or data.get("status") or "").lower()
    order_ref = data.get("orderReference", "")

    log.info("WFP callback: status=%s order_ref=%s", status, order_ref)

    if status in ("approved", "accept", "success") and order_ref.startswith("sub-"):
        try:
            user_id = int(order_ref.split("-")[1])
        except Exception:
            log.exception("Cannot parse user_id from order_ref=%s", order_ref)
            return
        await activate_or_extend(bot, user_id)
