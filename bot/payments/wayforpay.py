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

from bot.config import settings
from bot.services import _tz_aware_utc
from bot.db import Session, Subscription, Payment, PaymentToken

log = logging.getLogger("bot.payments")
WFP_API = "https://api.wayforpay.com/api"

# ---------- helpers ----------
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
    products_name_str = ";".join(product_names)
    products_count_str = ";".join(map(str, product_counts))
    products_price_str = ";".join(product_prices)
    return f"{merchant};{domain};{order_ref};{order_date};{amount_str};{currency};{products_name_str};{products_count_str};{products_price_str}"

# ---------- WayForPay signature ----------
def validate_wfp_signature(data: Dict[str, Any]) -> bool:
    signature_from_wfp = data.get("merchantSignature")
    if not signature_from_wfp:
        log.warning("Callback missing merchantSignature: %s", data)
        return False

    try:
        order_ref = data.get("orderReference") or data.get("orderRef") or ""
        amount = str(data.get("amount") or "0")
        currency = str(data.get("currency") or "")
        order_date = int(data.get("orderDate", time.time()))

        product_names = data.get("productName") or [""]
        product_counts = data.get("productCount") or [1]
        product_prices = data.get("productPrice") or [amount]

        base = make_base(
            merchant=settings.WFP_MERCHANT.strip(),
            domain=settings.WFP_DOMAIN.strip(),
            order_ref=order_ref,
            order_date=order_date,
            amount_str=amount,
            currency=currency,
            product_names=product_names,
            product_counts=product_counts,
            product_prices=product_prices
        )

        expected_sig = hmac_md5_hex(base, settings.WFP_SECRET.strip())
        if expected_sig != signature_from_wfp:
            log.warning("Invalid merchantSignature. Expected %s, got %s", expected_sig, signature_from_wfp)
            return False
        return True
    except Exception:
        log.exception("Error verifying callback signature: %s", data)
        return False

# ---------- public API ----------
async def create_invoice(
    user_id: int,
    amount: float,
    currency: str = "UAH",
    product_name: str = "Access to course (1 month)",
    start_token: str | None = None,
) -> tuple[str, str]:
    order_date = int(time.time())
    order_ref = f"sub-{user_id}-{order_date}-{uuid.uuid4().hex[:6]}"

    merchant = settings.WFP_MERCHANT.strip()
    domain = settings.WFP_DOMAIN.strip()
    secret = settings.WFP_SECRET.strip()

    amt = money2(amount)

    product_names = [product_name]
    product_counts = [1]
    product_prices = [amt]

    base = make_base(merchant, domain, order_ref, order_date, amt, currency,
                     product_names, product_counts, product_prices)
    signature = hmac_md5_hex(base, secret)

    ret_base = settings.BASE_URL.rstrip("/") + "/wfp/return"
    return_url = f"{ret_base}?token={start_token}" if start_token else ret_base
    service_url = settings.BASE_URL.rstrip("/") + "/payments/wayforpay/callback"

    payload = {
        "transactionType": "CREATE_INVOICE",
        "merchantAccount": merchant,
        "merchantDomainName": domain,
        "apiVersion": 1,
        "orderReference": order_ref,
        "orderDate": order_date,
        "amount": amt,
        "currency": currency,
        "productName": product_names,
        "productPrice": product_prices,
        "productCount": product_counts,
        "returnUrl": return_url,
        "serviceUrl": service_url,
        "merchantSignature": signature,
    }

    log.warning("📤 WFP payload: %s", {k: v for k, v in payload.items() if k != "merchantSignature"})
    log.warning("🔧 base = %s", base)
    log.warning("🔑 signature = %s", signature)

    async with httpx.AsyncClient(timeout=25) as cli:
        r = await cli.post(WFP_API, json=payload)
        r.raise_for_status()
        data = r.json()
        log.info("📥 WFP response: %s", data)

    url = data.get("invoiceUrl") or data.get("formUrl") or data.get("url")
    if not url:
        raise RuntimeError(f"WayForPay error: {data.get('reasonCode')} — {data.get('reason')}")
    
    return url, order_ref

async def process_callback(bot, data: Dict[str, Any]) -> None:
    try:
        if not validate_wfp_signature(data):
            log.info("⚠️ Callback signature failed: %s", data)
            return

        status = (data.get("transactionStatus") or data.get("status") or "").lower()
        order_ref = data.get("orderReference", "")
        amount = str(data.get("amount") or "0")
        currency = str(data.get("currency") or "")
        log.info("✅ WFP callback received: %s %s", status, order_ref)

        if not (status in ("approved", "accept", "success") and order_ref.startswith("sub-")):
            log.info("Ignored WFP callback: status=%s order_ref=%s", status, order_ref)
            return

        try:
            _, uid_str, ts_str, *_ = order_ref.split("-")
            user_id = int(uid_str)
            order_ts = int(ts_str)
            order_dt = datetime.fromtimestamp(order_ts, tz=timezone.utc)
        except Exception:
            log.info("🚫 Cannot parse order_ref: %s", order_ref)
            return

        async with Session() as s:
            # Проверяем дубли
            res = await s.execute(select(Payment).where(Payment.order_ref == order_ref))
            pay = res.scalar_one_or_none()
            if pay and pay.status == "approved":
                log.info("↩︎ Duplicate callback ignored: %s", order_ref)
                return

            # Проверяем устаревшие callback после отмены подписки
            sub = await s.get(Subscription, user_id)
            if sub and sub.updated_at and _tz_aware_utc(sub.updated_at) > order_dt:
                log.info("⛔ Stale callback ignored (after unsubscribe): %s", order_ref)
                return

            # Сохраняем или обновляем платеж
            if pay:
                pay.status = "approved"
                pay.amount = amount
                pay.currency = currency
            else:
                pay = Payment(
                    user_id=user_id,
                    order_ref=order_ref,
                    amount=float(amount) if amount else 0.0,
                    currency=currency,
                    status="approved",
                )
                s.add(pay)
            await s.commit()
            log.info("💰 Payment recorded: user=%s order_ref=%s", user_id, order_ref)

            # Помечаем последний pending токен как paid
            res = await s.execute(
                select(PaymentToken)
                .where(PaymentToken.user_id == user_id, PaymentToken.status == "pending")
                .order_by(PaymentToken.created_at.desc())
            )
            token_obj = res.scalars().first()
            if token_obj:
                token_obj.status = "paid"
                await s.commit()
                log.info("💎 Token marked as PAID for user %s: %s", user_id, token_obj.token)

    except Exception:
        log.exception("Unhandled error in WFP callback handler")
