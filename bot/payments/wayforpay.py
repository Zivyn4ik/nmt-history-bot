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
        # Логируем callback для отладки
        log.info("🔔 WFP callback received: %s", data)

        order_ref = str(data.get("orderReference") or data.get("orderRef") or "")
        amount = str(data.get("amount") or "0")
        currency = str(data.get("currency") or "")

        order_date_raw = data.get("orderDate")
        order_date = int(order_date_raw) if order_date_raw else int(time.time())

        def ensure_list(val):
            if isinstance(val, list):
                return [str(x) for x in val]
            if val is None:
                return []
            return [str(val)]

        product_names = ensure_list(data.get("productName"))
        product_counts = ensure_list(data.get("productCount"))
        product_prices = ensure_list(data.get("productPrice"))

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

        expected_sig = hmac_md5_hex(base, settings.WFP_SECRET.strip())
        if expected_sig != signature_from_wfp:
            log.warning(
                "Invalid merchantSignature. Expected=%s, got=%s, base=%s",
                expected_sig, signature_from_wfp, base
            )
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
    order_ref = str(order_ref)

    merchant = settings.WFP_MERCHANT.strip()
    domain = settings.WFP_DOMAIN.strip()
    secret = settings.WFP_SECRET.strip()
    amt = money2(amount)

    product_names = [product_name]
    product_counts = [1]
    product_prices = [amt]

    base = make_base(
        merchant=merchant,
        domain=domain,
        order_ref=order_ref,
        order_date=order_date,
        amount_str=amt,
        currency=currency,
        product_names=product_names,
        product_counts=product_counts,
        product_prices=product_prices,
    )
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

    log.warning("📤 WFP payload ready: %s", {k: v for k, v in payload.items() if k != "merchantSignature"})
    log.warning("🔧 base = %s", base)
    log.warning("🔑 signature = %s", signature)

    async with httpx.AsyncClient(timeout=25) as cli:
        try:
            r = await cli.post(WFP_API, json=payload)
            r.raise_for_status()
            data = r.json()
        except Exception as e:
            log.exception("Error creating invoice via WayForPay: %s", e)
            raise RuntimeError(f"Cannot create invoice: {e}")

    url = data.get("invoiceUrl") or data.get("formUrl") or data.get("url")
    if not url:
        log.error("WayForPay response missing invoice URL: %s", data)
        raise RuntimeError(f"WayForPay error: {data.get('reasonCode')} — {data.get('reason')}")

    return url, order_ref


async def process_callback(bot, data: Dict[str, Any]) -> None:
    try:
        if not validate_wfp_signature(data):
            log.info("⚠️ Callback signature failed: %s", data)
            return

        status = (data.get("transactionStatus") or data.get("status") or "").lower()
        log.info("✅ WFP callback received: status=%s", status)

        if status not in ("approved", "accept", "success"):
            log.info("Ignored WFP callback: status=%s", status)
            return

        async with Session() as s:
            # Находим последний pending или paid токен по user_id
            res = await s.execute(
                select(PaymentToken)
                .where(PaymentToken.status.in_(["pending", "paid"]))
                .order_by(PaymentToken.created_at.desc())
            )
            token_obj = res.scalars().first()
            if not token_obj:
                log.warning("⚠️ No token found for callback: %s", data)
                return

            user_id = token_obj.user_id

            # Обновляем PaymentToken
            if token_obj.status != "paid":
                token_obj.status = "paid"
                await s.commit()
                log.info("💎 Token marked as PAID for user %s: %s", user_id, token_obj.token)
            else:
                log.info("🔁 Token already paid for user %s: %s", user_id, token_obj.token)

        # Активируем подписку и выдаём доступ пользователю
        await activate_or_extend(bot, user_id)

    except Exception:
        log.exception("Unhandled error in WFP callback handler")
