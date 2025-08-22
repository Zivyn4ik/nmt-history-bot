# bot/payments/wayforpay.py
from __future__ import annotations

import time
import uuid
import hmac
import hashlib
import logging
from decimal import Decimal, ROUND_HALF_UP
from typing import Dict, Any
from datetime import datetime, timezone

import httpx
from sqlalchemy import select

from ..config import settings
from ..services import activate_or_extend
from ..db import Session, Subscription

log = logging.getLogger("payments.wfp")


def _sign(values: list[str], secret: str) -> str:
    data = ";".join(values).encode("utf-8")
    return hmac.new(secret.encode("utf-8"), data, hashlib.md5).hexdigest()


async def create_invoice(
    user_id: int,
    amount: float,
    currency: str,
    product_name: str,
) -> str:
    """
    Формирует ссылку WayForPay (инвойс) через API /invoice
    Возвращает URL на оплату.
    """
    amt = Decimal(str(amount)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    order_ref = f"{user_id}-{int(time.time())}"
    order_date = int(time.time())

    payload: Dict[str, Any] = {
        "transactionType": "CREATE_INVOICE",
        "merchantAccount": settings.WFP_MERCHANT,
        "merchantDomainName": settings.WFP_DOMAIN,
        "orderReference": order_ref,
        "orderDate": order_date,
        "amount": float(amt),
        "currency": currency,
        "productName": [product_name],
        "productPrice": [float(amt)],
        "productCount": [1],
        "serviceUrl": f"{settings.BASE_URL}/wfp/callback",
        "clientAccountId": str(user_id),
    }

    # подпись по докам WFP
    sign_order = [
        settings.WFP_MERCHANT,
        settings.WFP_DOMAIN,
        order_ref,
        str(order_date),
        str(float(amt)),
        currency,
        product_name,
        "1",
        str(float(amt)),
    ]
    payload["merchantSignature"] = _sign(sign_order, settings.WFP_SECRET)

    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.post("https://api.wayforpay.com/api", json=payload)
        r.raise_for_status()
        js = r.json()

    if "invoiceUrl" not in js:
        raise RuntimeError(f"WFP не повернув invoiceUrl: {js}")

    return js["invoiceUrl"]


async def process_callback(request, bot) -> Dict[str, Any]:
    """
    Обработчик callback от WFP (FastAPI будет передавать Request).
    Принимаем только approved платежи и активируем доступ.
    """
    data = await request.json()
    log.info("WFP callback: %s", data)

    # минимум полей
    required = ["merchantAccount", "orderReference", "transactionStatus", "amount", "currency", "clientAccountId"]
    for k in required:
        if k not in data:
            return {"error": f"missing {k}"}

    # проверка подписи
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

    if data.get("transactionStatus") != "Approved":
        # ничего не делаем, подтверждения нет
        return {"status": "ignored"}

    user_id = int(data.get("clientAccountId"))
    # активируем/продлеваем
    await activate_or_extend(bot, user_id)

    return {"status": "ok"}
