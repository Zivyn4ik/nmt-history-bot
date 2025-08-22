from __future__ import annotations

import time
import hmac
import hashlib
import logging
from decimal import Decimal, ROUND_HALF_UP
from typing import Dict, Any
from urllib.parse import urlparse

import httpx

from ..config import settings
from ..services import activate_or_extend

log = logging.getLogger("payments.wfp")
WFP_API_URL = "https://api.wayforpay.com/api"


# ---------- helpers ----------

def _money2(x: float | int | str) -> str:
    """Строка с двумя знаками после запятой (WFP очень чувствителен к формату)."""
    return str(Decimal(str(x)).quantize(Decimal("0.00"), rounding=ROUND_HALF_UP))

def _sign_str(values: list[str], secret: str) -> str:
    data = ";".join(values).encode("utf-8")
    return hmac.new(secret.encode("utf-8"), data, hashlib.md5).hexdigest()

def _canonical_domain(d: str) -> str:
    """Без схемы, без слешей, в нижнем регистре — как ожидает WFP."""
    d = (d or "").strip()
    if d.startswith(("http://", "https://")):
        p = urlparse(d)
        d = p.netloc or p.path
    return d.strip("/").lower()


# ---------- public API ----------

async def create_invoice(
    user_id: int,
    amount: float,
    currency: str,
    product_name: str,
) -> str:
    """
    Создаем инвойс и возвращаем invoiceUrl.
    НИЧЕГО не меняем в вашей логике кроме:
      - apiVersion добавлен (иначе 1129),
      - домен приводим к каноническому виду,
      - формат суммы фиксируем до 2 знаков и используем тот же в подписи.
    """
    order_date = int(time.time())
    order_ref = f"sub-{user_id}-{order_date}"

    merchant = (settings.WFP_MERCHANT or "").strip()
    domain = _canonical_domain(settings.WFP_DOMAIN)
    secret = (settings.WFP_SECRET or "").strip()

    amt_str = _money2(amount)

    payload: Dict[str, Any] = {
        "apiVersion": 1,                         # нужно для WFP
        "language": "UA",
        "transactionType": "CREATE_INVOICE",
        "merchantAccount": merchant,
        "merchantDomainName": domain,            # в подписи используем ровно это значение
        "orderReference": order_ref,
        "orderDate": order_date,
        "amount": amt_str,                       # строка, как и в вашей рабочей версии
        "currency": currency,
        "productName": [product_name],
        "productCount": [1],
        "productPrice": [amt_str],               # строка и совпадает с подписью
        "serviceUrl": settings.BASE_URL.rstrip("/") + "/wfp/callback",
        # returnUrl можно не задавать — платёж всё равно валиден
    }

    # merchantSignature — строго из тех же значений, что в payload
    sign_parts = [
        payload["merchantAccount"],
        payload["merchantDomainName"],
        payload["orderReference"],
        str(payload["orderDate"]),
        str(payload["amount"]),
        payload["currency"],
        product_name,
        "1",
        str(payload["productPrice"][0]),
    ]
    payload["merchantSignature"] = _sign_str(sign_parts, secret)

    # (опционально) отладка
    log.debug("WFP sign_parts=%s", sign_parts)

    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.post(WFP_API_URL, json=payload)
        r.raise_for_status()
        js = r.json()

    url = js.get("invoiceUrl") or js.get("formUrl") or js.get("url")
    if not url:
        log.error("WFP error while creating invoice: %s", js)
        raise RuntimeError(f"WFP не повернув invoiceUrl: {js}")

    return url


async def process_callback(request, bot) -> Dict[str, Any]:
    """
    Совместимая с вашим app.py обертка:
    app вызывает process_callback(request, bot)
    """
    data = await request.json()
    log.info("WFP callback: %s", data)

    required = [
        "merchantAccount",
        "orderReference",
        "transactionStatus",
        "amount",
        "currency",
        "clientAccountId",
        "merchantSignature",
    ]
    for k in required:
        if k not in data:
            return {"error": f"missing {k}"}

    # проверяем подпись коллбека
    sign_src = [
        data.get("merchantAccount", ""),
        data.get("orderReference", ""),
        str(data.get("amount", "")),
        data.get("currency", ""),
        data.get("authCode", ""),
        data.get("cardPan", ""),
        data.get("transactionStatus", ""),
        data.get("reasonCode", ""),
    ]
    expected = _sign_str(sign_src, (settings.WFP_SECRET or "").strip())
    if data.get("merchantSignature") != expected:
        return {"error": "bad signature"}

    # активируем доступ только для Approved
    if str(data.get("transactionStatus")).lower() == "approved":
        try:
            user_id = int(data.get("clientAccountId"))
        except Exception:
            return {"error": "bad clientAccountId"}
        await activate_or_extend(bot, user_id)
        return {"status": "ok"}

    return {"status": "ignored"}
