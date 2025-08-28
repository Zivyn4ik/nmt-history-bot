from __future__ import annotations

import uuid
import hashlib
import hmac
import time
import logging from decimal
import Decimal, ROUND_HALF_UP from typing
import Dict, Any from datetime
import datetime, timezone
import httpx from sqlalchemy
import select
import aiohttp


from typing import Any, Dict, Tuple

from bot.config import settings
from bot.services import _tz_aware_utc, activate_or_extend
from bot.db import Session, Subscription, Payment, PaymentToken

log = logging.getLogger("bot.payments") WFP_API = "https://api.wayforpay.com/api"

def _sign_create_invoice(payload: Dict[str, Any]) -> str:
    parts = [
        payload["merchantAccount"],
        payload["merchantDomainName"],
        payload["orderReference"],
        str(payload["orderDate"]),
        str(payload["amount"]),
        payload["currency"],
    ]
    # Массивы — по порядку
    parts.extend(payload["productName"])
    parts.extend(str(x) for x in payload["productCount"])
    parts.extend(str(x) for x in payload["productPrice"])
    data = ";".join(parts)
    return hmac.new(
        settings.WFP_SECRET.encode("utf-8"),
        data.encode("utf-8"),
        hashlib.md5,
    ).hexdigest()

# Для CHECK_STATUS: merchantAccount;orderReference
def _sign_check_status(merchant: str, order_ref: str) -> str:
    data = f"{merchant};{order_ref}"
    return hmac.new(
        settings.WFP_SECRET.encode("utf-8"),
        data.encode("utf-8"),
        hashlib.md5,
    ).hexdigest()

async def create_invoice(user_id: int) -> Tuple[str, str]:
    """Возвращает (orderReference, invoiceUrl)"""
    now = int(time.time())
    order_reference = f"{user_id}-{now}"
    payload = {
        "transactionType": "CREATE_INVOICE",
        "merchantAccount": settings.WFP_MERCHANT,
        "merchantDomainName": settings.WFP_DOMAIN,
        "apiVersion": 1,
        "language": "UA",
        "orderReference": order_reference,
        "orderDate": now,
        "amount": settings.PRICE,
        "currency": settings.CURRENCY,
        "productName": [settings.PRODUCT_NAME],
        "productPrice": [settings.PRICE],
        "productCount": [1],
        # важно: без callback — используем returnUrl для редиректа
        "returnUrl": settings.return_url,
        # опционально: client info
        "clientAccountId": str(user_id),
    }
    payload["merchantSignature"] = _sign_create_invoice(payload)

    async with aiohttp.ClientSession() as session:
        async with session.post(settings.service_url, json=payload, timeout=30) as resp:
            data = await resp.json()
            if data.get("reasonCode") not in (1100, 1108):  # 1100 OK, 1108 duplicate OK
                raise RuntimeError(f"WFP create_invoice error: {data}")
            invoice_url = data["invoiceUrl"]
            return order_reference, invoice_url

async def check_status(order_reference: str) -> Dict[str, Any]:
    payload = {
        "transactionType": "CHECK_STATUS",
        "merchantAccount": settings.WFP_MERCHANT,
        "orderReference": order_reference,
    }
    payload["merchantSignature"] = _sign_check_status(settings.WFP_MERCHANT, order_reference)
    async with aiohttp.ClientSession() as session:
        async with session.post(settings.service_url, json=payload, timeout=20) as resp:
            return await resp.json()

