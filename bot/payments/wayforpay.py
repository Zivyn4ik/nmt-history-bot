from __future__ import annotations

import time
import uuid
import hmac
import hashlib
import logging
from decimal import Decimal, ROUND_HALF_UP
from typing import Dict, Any, Optional
from datetime import datetime, timezone
from urllib.parse import urlparse

import httpx
from sqlalchemy import select

from ..config import settings
from ..services import activate_or_extend
from ..db import Session, Subscription, Payment  # для идемпотентности

log = logging.getLogger("bot.payments")
WFP_API = "https://api.wayforpay.com/api"

# ---------- helpers ----------
def money2(x: float | int | str) -> str:
    """Строгий формат денег: всегда 2 знака после запятой (строка)."""
    return str(Decimal(str(x)).quantize(Decimal("0.00"), rounding=ROUND_HALF_UP))

def hmac_md5_hex(message: str, secret: str) -> str:
    """HMAC-MD5 как требует WFP."""
    return hmac.new(
        secret.strip().encode("utf-8"),
        message.strip().encode("utf-8"),
        hashlib.md5
    ).hexdigest()

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

def canonical_domain(d: str) -> str:
    """
    Приводим домен к виду, который ожидает WFP в подписи:
    - без схемы (https://)
    - без слешей
    - в нижнем регистре
    """
    d = d.strip()
    if d.startswith(("http://", "https://")):
        p = urlparse(d)
        d = (p.netloc or p.path)
    return d.strip("/").lower()

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
    domain = canonical_domain(settings.WFP_DOMAIN)  # <-- ключевое изменение
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
        "apiVersion": 1,  # требуется WFP, иначе 1129
        "orderReference": order_ref,
        "orderDate": order_date,
        "amount": amt,                # оставляем строкой, как в вашей рабочей версии
        "currency": currency,
        "productName": [product_name],
        "productPrice": [amt],        # строкой и в подписи — идентично
        "productCount": [1],
        "returnUrl": return_url,
        "serviceUrl": service_url,
        "merchantSignature": signature,
    }

    # Диагностика (удобно смотреть в логи Render при проблемах подписи)
    print("📤 WFP payload:", {k: v for k, v in payload.items() if k != "merchantSignature"})
    print("🔧 sign_base =", base)
    print("🔑 signature =", signature)

    async with httpx.AsyncClient(timeout=25) as cli:
        r = await cli.post(WFP_API, json=payload)
        r.raise_for_status()
        data = r.json()
        print("📥 WFP response:", data)

    url = data.get("invoiceUrl") or data.get("formUrl") or data.get("url")
    if not url:
        raise RuntimeError(f"WayForPay error: {data.get('reasonCode')} — {data.get('reason')}")
    return url


def verify_callback_signature(_data: Dict[str, Any]) -> bool:
    # при необходимости можно добавить полноценную проверку
    return True


async def process_callback(bot, data: Dict[str, Any]) -> None:
    """
    Идемпотентная обработка коллбэка:
    - игнор дубликатов по orderReference;
    - игнор «старых» коллбеков, если после инвойса успели сделать wipe/unsubscribe;
    - запись Payment и продление доступа.
    """
    try:
        if not verify_callback_signature(data):
            print("⚠️ Callback signature failed:", data)
            return

        status = (data.get("transactionStatus") or data.get("status") or "").lower()
        order_ref = data.get("orderReference", "")
        amount = str(data.get("amount") or "0")
        currency = str(data.get("currency") or "")
        print("✅ WFP callback received:", status, order_ref)

        if not (status in ("approved", "accept", "success") and order_ref.startswith("sub-")):
            return

        try:
            _, uid_str, ts_str, *_ = order_ref.split("-")
            user_id = int(uid_str)
            order_ts = int(ts_str)
            order_dt = datetime.fromtimestamp(order_ts, tz=timezone.utc)
        except Exception:
            print("🚫 Cannot parse order_ref:", order_ref)
            return

        # 1) защита от дубликатов по order_ref
        async with Session() as s:
            res = await s.execute(select(Payment).where(Payment.order_ref == order_ref))
            pay = res.scalar_one_or_none()
            if pay and pay.status == "approved":
                print("↩︎ Duplicate callback ignored:", order_ref)
                return

        # 2) защита от «старых» коллбеков после /unsubscribe
        async with Session() as s:
            sub = await s.get(Subscription, user_id)
            if sub and sub.updated_at and sub.updated_at.replace(tzinfo=timezone.utc) > order_dt:
                print("⛔ Stale callback ignored (wiped after invoice):", order_ref)
                return

        # 3) фиксируем/обновляем запись платежа
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

        # 4) продлеваем доступ
        await activate_or_extend(bot, user_id)

    except Exception:
        log.exception("Unhandled error in WFP callback handler")
