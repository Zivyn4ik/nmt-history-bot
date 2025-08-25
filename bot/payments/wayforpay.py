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
from bot.db import Session, Subscription, Payment

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

# ---------- public API ----------
async def create_invoice(
    user_id: int,
    amount: float,
    currency: str = "UAH",
    product_name: str = "Access to course (1 month)",
) -> str:
    order_date = int(time.time())
    order_ref = f"sub-{user_id}-{order_date}-{uuid.uuid4().hex[:6]}"

    merchant = settings.WFP_MERCHANT.strip()
    domain = settings.WFP_DOMAIN.strip()
    secret = settings.WFP_SECRET.strip()

    amt = money2(amount)
    base = make_base(merchant, domain, order_ref, order_date, amt, currency, product_name, 1, amt)
    signature = hmac_md5_hex(base, secret)

    return_url = settings.BASE_URL.rstrip("/") + "/wfp/return"
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

    log.warning("📤 WFP payload:", {k: v for k, v in payload.items() if k != "merchantSignature"})
    log.warning("🔧 base =", base)
    log.warning("🔑 signature =", signature)

    async with httpx.AsyncClient(timeout=25) as cli:
        try:
            r = await cli.post(WFP_API, json=payload)
            r.raise_for_status()
            data = r.json()
            log.info("📥 WFP response: %s", data)
        except httpx.RequestError as e:
            log.error("HTTP error while creating invoice: %s", e)
            raise
        except httpx.HTTPStatusError as e:
            log.error("WayForPay returned error status: %s - %s", e.response.status_code, e.response.text)
            raise
        except Exception as e:
            log.exception("Unexpected error during invoice creation: %s", e)
            raise

    url = data.get("invoiceUrl") or data.get("formUrl") or data.get("url")
    if not url:
        raise RuntimeError(f"WayForPay error: {data.get('reasonCode')} — {data.get('reason')}")
    return url


def verify_callback_signature(data: Dict[str, Any]) -> bool:
    signature_from_wfp = data.get("merchantSignature")
    if not signature_from_wfp:
        log.warning("Callback missing merchantSignature: %s", data)
        return False

    try:
        order_ref = data.get("orderReference", "")
        amount = str(data.get("amount") or "0")
        currency = str(data.get("currency") or "")
        product_name = data.get("productName", [""])[0]  # если список
        order_date = int(data.get("orderDate", time.time()))

        base=make_base(
            merchant=settings.WFP_MERCHANT.strip(),
            domain=settings.WFP_DOMAIN.strip(),
            order_ref=order_ref,
            order_date=order_date,
            amount_str=amount,
            currency=currency,
            product_name=product_name,
            product_count=1,
            product_price_str=amount
        )
        expected_sig = hmac_md5_hex(base, settings.WFP_SECRET.strip())
        if expected_sig != signature_from_wfp:
            log.warning("Invalid merchantSignature. Expected %s, got %s", expected_sig, signature_from_wfp)
            return False
        return True
    except Exception:
        log.exception("Error verifying callback signature: %s", data)
        return False


async def process_callback(bot, data: Dict[str, Any]) -> None:
    """
    Идемпотентная обработка коллбэка WayForPay.
    Защита от дубликатов и старых callback’ов после /unsubscribe.
    Обновляет платеж и подписку, отправляет сообщение пользователю.
    """
    try:
        if not verify_callback_signature(data):
            log.info("⚠️ Callback signature failed:", data)
            return

        status = (data.get("transactionStatus") or data.get("status") or "").lower()
        order_ref = data.get("orderReference", "")
        amount = str(data.get("amount") or "0")
        currency = str(data.get("currency") or "")
        log.info("✅ WFP callback received:", status, order_ref)

        if not (status in ("approved", "accept", "success") and order_ref.startswith("sub-")):
            return

        # Парсим user_id и timestamp заказа
        try:
            _, uid_str, ts_str, *_ = order_ref.split("-")
            user_id = int(uid_str)
            order_ts = int(ts_str)
            order_dt = datetime.fromtimestamp(order_ts, tz=timezone.utc)
        except Exception:
            log.info("🚫 Cannot parse order_ref:", order_ref)
            return

        # Проверка дубликата платежа
        async with Session() as s:
            res = await s.execute(select(Payment).where(Payment.order_ref == order_ref))
            pay = res.scalar_one_or_none()
            if pay and pay.status == "approved":
                log.info("↩︎ Duplicate callback ignored:", order_ref)
                return

        # Проверка «старого» callback после /unsubscribe
        async with Session() as s:
            sub = await s.get(Subscription, user_id)
            if sub and sub.updated_at and _tz_aware_utc(sub.updated_at) > order_dt:
                log.info("⛔ Stale callback ignored (after unsubscribe):", order_ref)
                return

        # Фиксируем или создаём платеж
        async with Session() as s:
            if pay:
                pay.status = "approved"
                pay.amount = amount
                pay.currency = currency
            else:
                pay = Payment(
                    user_id=user_id,
                    order_ref=order_ref,
                    amount=amount,
                    currency=currency,
                    status="approved",
                )
                s.add(pay)
            await s.commit()
            log.info(f"💰 Payment recorded: user={user_id}, order_ref={order_ref}")

        # Продлеваем или активируем подписку
        await activate_or_extend(bot, user_id)
        
# --- Отправка персональной ссылки пользователю ---
invite_url = f"{settings.TG_JOIN_REQUEST_URL}?start={user_id}"
try:
    await bot.send_message(
        chat_id=user_id,
        text=(
            f"✅ Оплата успішна!\n"
            f"Перейдіть за вашим особистим посиланням у Telegram:\n"
            f"{invite_url}"
        )
    )
    log.info(f"📩 Personal invite sent to user {user_id}")
except Exception as e:
    log.warning(f"Не удалось отправить ссылку пользователю {user_id}: {e}")
    
log.info(f"✅ Subscription activated/extended for user {user_id}")

    except Exception:

        log.exception("Unhandled error in WFP callback handler")
