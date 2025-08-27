from __future__ import annotations

import time
import uuid
import hmac
import hashlib
import logging
from decimal import Decimal, ROUND_HALF_UP
from typing import Dict, Any, Optional
from datetime import datetime, timezone

import httpx
from sqlalchemy import select

from bot.config import settings
from bot.services import activate_or_extend, _tz_aware_utc
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

# ---------- WayForPay signature ----------
def validate_wfp_signature(data: Dict[str, Any]) -> bool:
    """
    Проверяет merchantSignature WayForPay.
    Поддерживает orderReference и orderRef.
    """
    signature_from_wfp = data.get("merchantSignature")
    if not signature_from_wfp:
        log.warning("Callback missing merchantSignature: %s", data)
        return False

    try:
        # Поддержка разных ключей для orderReference
        order_ref = data.get("orderReference") or data.get("orderRef") or ""
        amount = str(data.get("amount") or "0")
        currency = str(data.get("currency") or "")
        product_name = (data.get("productName") or [""])[0]
        order_date = int(data.get("orderDate", time.time()))

        base = make_base(
            merchant=settings.WFP_MERCHANT.strip(),
            domain=settings.WFP_DOMAIN.strip(),
            order_ref=order_ref,
            order_date=order_date,
            amount_str=amount,
            currency=currency,
            product_name=product_name,
            product_count=1,
            product_price_str=amount,
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
) -> tuple[str, str]:  # возвращаем (url, order_ref)
    order_date = int(time.time())
    order_ref = f"sub-{user_id}-{order_date}-{uuid.uuid4().hex[:6]}"

    merchant = settings.WFP_MERCHANT.strip()
    domain = settings.WFP_DOMAIN.strip()
    secret = settings.WFP_SECRET.strip()

    amt = money2(amount)
    base = make_base(merchant, domain, order_ref, order_date, amt, currency, product_name, 1, amt)
    signature = hmac_md5_hex(base, secret)

    # ⬇️ возвращаем пользователя в ваш бекенд с токеном, чтобы затем уйти в t.me/<bot>?start=<token>
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
        "productName": [product_name],
        "productPrice": [amt],
        "productCount": [1],
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
    """
    Идемпотентная обработка коллбэка WayForPay.
    Обновляет таблицу payments и переводит последний pending-токен пользователя в paid.
    """
    try:
        if not validate_wfp_signature(data):
            log.info("⚠️ Callback signature failed: %s", data)
            return

        status = (data.get("transactionStatus") or data.get("status") or "").lower()
        order_ref = data.get("orderReference", "")
        amount = str(data.get("amount") or "0")
        currency = str(data.get("currency") or "")
        log.info("✅ WFP callback received: %s %s", status, order_ref)

        # only approved payments for our auto-created order refs
        if not (status in ("approved", "accept", "success") and order_ref.startswith("sub-")):
            log.info("Ignored WFP callback: status=%s order_ref=%s", status, order_ref)
            return

        # parse user_id and timestamp from order_ref (format sub-<user>-<ts>-...)
        try:
            _, uid_str, ts_str, *_ = order_ref.split("-")
            user_id = int(uid_str)
            order_ts = int(ts_str)
            order_dt = datetime.fromtimestamp(order_ts, tz=timezone.utc)
        except Exception:
            log.info("🚫 Cannot parse order_ref: %s", order_ref)
            return

        # check duplicate payment
        async with Session() as s:
            res = await s.execute(select(Payment).where(Payment.order_ref == order_ref))
            pay = res.scalar_one_or_none()
            if pay and pay.status == "approved":
                log.info("↩︎ Duplicate callback ignored: %s", order_ref)
                return

        # check stale callback after unsubscribe
        async with Session() as s:
            sub = await s.get(Subscription, user_id)
            if sub and sub.updated_at and _tz_aware_utc(sub.updated_at) > order_dt:
                log.info("⛔ Stale callback ignored (after unsubscribe): %s", order_ref)
                return

        # record or update payment
        async with Session() as s:
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

        # ✅ Переводим ПОСЛЕДНИЙ pending-токен этого пользователя в paid
        async with Session() as s:
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

        # ⛔ Специальных сообщений тут не шлём — весь поток завершится через /start <token>

    except Exception:
        log.exception("Unhandled error in WFP callback handler")
