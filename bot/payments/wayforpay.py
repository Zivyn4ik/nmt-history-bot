from __future__ import annotations

import time
import uuid
import hmac
import hashlib
import logging
from decimal import Decimal, ROUND_HALF_UP
from typing import Dict, Any
from datetime import datetime

import httpx
from sqlalchemy import select

from bot.config import settings
from bot.services import activate_or_extend
from bot.db import Session, PaymentToken

log = logging.getLogger("bot.payments")
WFP_API = "https://api.wayforpay.com/api"

def money2(x: float | int | str) -> str:
    return str(Decimal(str(x)).quantize(Decimal("0.00"), rounding=ROUND_HALF_UP))

def hmac_md5_hex(message: str, secret: str) -> str:
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
    product_names: list[str],
    product_counts: list[int],
    product_prices: list[str],
) -> str:
    return f"{merchant};{domain};{order_ref};{order_date};{amount_str};{currency};" \
           f"{';'.join(product_names)};{';'.join(map(str, product_counts))};{';'.join(product_prices)}"

def validate_wfp_signature(data: Dict[str, Any]) -> bool:
    sig = data.get("merchantSignature")
    if not sig:
        log.warning("Callback missing merchantSignature: %s", data)
        return False

    try:
        order_ref = str(data.get("orderReference") or data.get("orderRef") or "")
        amount = str(data.get("amount") or "0")
        currency = str(data.get("currency") or "")
        order_date = int(data.get("orderDate") or time.time())

        product_names = data.get("productName") or []
        product_counts = data.get("productCount") or []
        product_prices = data.get("productPrice") or []

        if not isinstance(product_names, list):
            product_names = [product_names]
        if not isinstance(product_counts, list):
            product_counts = [product_counts]
        if not isinstance(product_prices, list):
            product_prices = [product_prices]

        base = make_base(
            merchant=settings.WFP_MERCHANT.strip(),
            domain=settings.WFP_DOMAIN.strip(),
            order_ref=order_ref,
            order_date=order_date,
            amount_str=amount,
            currency=currency,
            product_names=product_names,
            product_counts=product_counts,
            product_prices=product_prices,
        )

        expected = hmac_md5_hex(base, settings.WFP_SECRET.strip())
        if expected != sig:
            log.warning("Invalid merchantSignature. Expected=%s, got=%s", expected, sig)
            return False

        return True
    except Exception:
        log.exception("Error verifying callback signature: %s", data)
        return False

async def create_invoice(
    user_id: int,
    amount: float,
    currency: str = "UAH",
    product_name: str = "Access to course (1 month)",
    start_token: str | None = None,
) -> tuple[str, str]:
    order_date = int(time.time())
    order_ref = f"sub-{user_id}-{order_date}-{uuid.uuid4().hex[:6]}"
    amt = money2(amount)
    merchant = settings.WFP_MERCHANT.strip()
    domain = settings.WFP_DOMAIN.strip()
    secret = settings.WFP_SECRET.strip()

    base = make_base(
        merchant=merchant,
        domain=domain,
        order_ref=order_ref,
        order_date=order_date,
        amount_str=amt,
        currency=currency,
        product_names=[product_name],
        product_counts=[1],
        product_prices=[amt],
    )
    signature = hmac_md5_hex(base, secret)

    return_url = f"{settings.BASE_URL.rstrip('/')}/wfp/return"
    if start_token:
        return_url += f"?token={start_token}"
    service_url = f"{settings.BASE_URL.rstrip('/')}/payments/wayforpay/callback"

    payload = {
        "transactionType": "CREATE_INVOICE",
        "merchantAccount": merchant,
        "merchantDomainName": domain,
        "apiVersion": 1,
        "orderReference": order_ref,
        "orderDate": order_date,
        "amount": amt,
        "currency": currency,
        "productName": [product_name],
        "productPrice": [amt],
        "productCount": [1],
        "returnUrl": return_url,
        "serviceUrl": service_url,
        "merchantSignature": signature,
    }

    log.info("WFP payload: %s", {k: v for k, v in payload.items() if k != "merchantSignature"})

    async with httpx.AsyncClient(timeout=25) as cli:
        r = await cli.post(WFP_API, json=payload)
        r.raise_for_status()
        data = r.json()

    url = data.get("invoiceUrl") or data.get("formUrl") or data.get("url")
    if not url:
        raise RuntimeError(f"WayForPay error: {data}")

    return url, order_ref

async def process_callback(bot, data: Dict[str, Any]) -> None:
    if not validate_wfp_signature(data):
        return

    status = (data.get("transactionStatus") or data.get("status") or "").lower()
    if status not in ("approved", "accept", "success"):
        return

    async with Session() as s:
        res = await s.execute(
            select(PaymentToken).where(PaymentToken.status == "pending").order_by(PaymentToken.created_at.desc())
        )
        token_obj = res.scalars().first()
        if not token_obj:
            log.warning("No pending token found for callback")
            return

        token_obj.status = "paid"
        await s.commit()
        await activate_or_extend(bot, token_obj.user_id)
        log.info("Subscription activated for user %s", token_obj.user_id)
