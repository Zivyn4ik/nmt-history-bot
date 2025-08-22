# bot/payments/wayforpay.py
from __future__ import annotations

import time
import hmac
import hashlib
import logging
from decimal import Decimal, ROUND_HALF_UP
from typing import Dict, Any

import httpx

from ..config import settings
from ..services import activate_or_extend

log = logging.getLogger("payments.wfp")


def _sign(values: list[str], secret: str) -> str:
    """HMAC-MD5 подпись по документации WayForPay."""
    data = ";".join(values).encode("utf-8")
    return hmac.new(secret.encode("utf-8"), data, hashlib.md5).hexdigest()


async def create_invoice(
    user_id: int,
    amount: float,
    currency: str,
    product_name: str,
) -> str:
    """
    Создать инвойс WayForPay и вернуть ссылку на оплату (invoiceUrl).
    Требуемые поля по WFP: apiVersion, transactionType, merchantAccount,
    merchantDomainName, orderReference, orderDate, amount, currency,
    productName[], productCount[], productPrice[], serviceUrl, language.
    """
    # округляем сумму корректно до копеек
    amt = Decimal(str(amount)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    order_ref = f"{user_id}-{int(time.time())}"
    order_date = int(time.time())

    # тело запроса
    payload: Dict[str, Any] = {
        "apiVersion": 1,  # <-- обязательное поле
        "language": "UA",  # можно "EN"/"RU"
        "transactionType": "CREATE_INVOICE",
        "merchantAccount": settings.WFP_MERCHANT,
        "merchantDomainName": settings.WFP_DOMAIN,  # должен совпадать с доменом в кабинете WFP
        "orderReference": order_ref,
        "orderDate": order_date,
        "amount": float(amt),
        "currency": currency,
        "productName": [product_name],
        "productCount": [1],
        "productPrice": [float(amt)],
        "serviceUrl": f"{settings.BASE_URL}/wfp/callback",  # публично доступный HTTPS
        "clientAccountId": str(user_id),
    }

    # подпись merchantSignature (см. доки WFP)
    sign_order = [
        payload["merchantAccount"],
        payload["merchantDomainName"],
        payload["orderReference"],
        str(payload["orderDate"]),
        str(payload["amount"]),
        payload["currency"],
        product_name,
        "1",
        str(float(amt)),
    ]
    payload["merchantSignature"] = _sign(sign_order, settings.WFP_SECRET)

    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.post("https://api.wayforpay.com/api", json=payload)
        r.raise_for_status()
        js = r.json()

    # ожидаем invoiceUrl
    if "invoiceUrl" not in js:
        # отладочная запись в логи, чтобы видеть первопричину от WFP
        log.error("WFP error while creating invoice: %s", js)
        raise RuntimeError(f"WFP не повернув invoiceUrl: {js}")

    return js["invoiceUrl"]


async def process_callback(request, bot) -> Dict[str, Any]:
    """
    Обработка callback от WayForPay. Подтверждаем только Approved.
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

    # проверяем подпись WFP
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
    expected = _sign(sign_src, settings.WFP_SECRET)
    if data.get("merchantSignature") != expected:
        return {"error": "bad signature"}

    # активируем доступ только для подтвержденных платежей
    if data.get("transactionStatus") == "Approved":
        user_id = int(data.get("clientAccountId"))
        await activate_or_extend(bot, user_id)
        return {"status": "ok"}

    return {"status": "ignored"}
