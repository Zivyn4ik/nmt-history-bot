from __future__ import annotations
import time, uuid, base64, hashlib, logging
import httpx
from typing import Dict, Any, List
from ..config import settings
from ..services import activate_or_extend

log = logging.getLogger("bot.payments")
WFP_API = "https://api.wayforpay.com/api"

def _md5_hex(s: str) -> str:
    return hashlib.md5(s.encode("utf-8")).hexdigest()

def _signature_candidates(
    merchant: str,
    domain: str,
    order_ref: str,
    order_date: int,
    amount: float,
    currency: str,
    product_name: str,
    secret: str,
) -> List[str]:
    amt = f"{amount:.2f}"

    bases = [
        # Часто встречающийся вариант из доков WFP для CREATE_INVOICE
        f"{merchant};{domain};{order_ref};{order_date};{amt};{currency};{product_name};1;{amt}",
        # Вариант без order_date
        f"{merchant};{domain};{order_ref};{amt};{currency};{product_name};1;{amt}",
        # Короткий вариант (иногда включён в примерах)
        f"{merchant};{domain};{order_ref};{amt};{currency}",
    ]

    sigs = []
    for base in bases:
        # На практике у разных кабинетов используют:
        # 1) md5(base + secret)
        # 2) md5(secret + base)
        # 3) md5(base + ';' + secret)
        # 4) md5(secret + ';' + base)
        sigs.extend([
            _md5_hex(base + secret),
            _md5_hex(secret + base),
            _md5_hex(base + ";" + secret),
            _md5_hex(secret + ";" + base),
        ])
    # Оставляем только уникальные
    uniq = []
    seen = set()
    for s in sigs:
        if s not in seen:
            uniq.append(s)
            seen.add(s)
    return uniq

async def create_invoice(user_id: int, amount: float, currency: str = "UAH", product_name: str = "Channel subscription (1 month)") -> str:
    order_date = int(time.time())
    order_ref = f"sub-{user_id}-{order_date}-{uuid.uuid4().hex[:6]}"

    merchant = settings.WFP_MERCHANT
    domain = settings.WFP_DOMAIN
    secret = settings.WFP_SECRET

    # Подготовим общий payload без подписи
    base_payload = {
        "transactionType": "CREATE_INVOICE",
        "merchantAccount": merchant,
        "merchantDomainName": domain,
        "apiVersion": 1,
        "orderReference": order_ref,
        "orderDate": order_date,
        "amount": round(amount, 2),
        "currency": currency,
        "productName": [product_name],
        "productPrice": [round(amount,2)],
        "productCount": [1],
        "returnUrl": f"{settings.BASE_URL}/thanks",
        "serviceUrl": f"{settings.BASE_URL}/payments/wayforpay/callback",
    }

    # Перебираем кандидатов подписи до успеха
    for sig in _signature_candidates(merchant, domain, order_ref, order_date, amount, currency, product_name, secret):
        payload = dict(base_payload)
        payload["merchantSignature"] = sig
        try:
            async with httpx.AsyncClient(timeout=20) as cli:
                r = await cli.post(WFP_API, json=payload)
                r.raise_for_status()
                data = r.json()
        except Exception as e:
            log.exception("HTTP error calling WayForPay: %s", e)
            continue

        log.info("WFP response (try with signature=%s): %s", sig, data)
        invoice_url = data.get("invoiceUrl")
        if invoice_url:
            return invoice_url

        # Если ошибка не про подпись — дальше перебирать смысла нет
        reason = (data.get("reason") or "").lower()
        if "signature" not in reason and data.get("reasonCode") not in (1109, 1133):
            raise RuntimeError(f"WayForPay error: {data}")

    # Если все попытки не дали invoiceUrl
    raise RuntimeError("WayForPay error: подпись не принята. Проверьте WFP_SECRET, merchantAccount, merchantDomainName.")

def verify_callback_signature(data: Dict[str, Any]) -> bool:
    # При желании можно включить строгую проверку подписи колбэка.
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
