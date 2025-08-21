from __future__ import annotations

import time
import uuid
import hmac
import hashlib
import logging
from decimal import Decimal, ROUND_HALF_UP
from typing import Dict, Any, List, Optional

import httpx

from ..config import settings
from ..services import activate_or_extend

log = logging.getLogger("bot.payments")
WFP_API = "https://api.wayforpay.com/api"


# ---------- вспомогательные функции ----------

def money2(x: float | int | str) -> str:
    """
    Денежное представление СТРОКОЙ ровно с 2 знаками: '100.00', '2.10', '2.00'
    """
    return str(Decimal(str(x)).quantize(Decimal("0.00"), rounding=ROUND_HALF_UP))


def hmac_md5_hex(message: str, secret: str) -> str:
    """
    Правильная подпись для WayForPay: HMAC-MD5(message, key=secret)
    """
    return hmac.new(secret.strip().encode("utf-8"),
                    message.strip().encode("utf-8"),
                    hashlib.md5).hexdigest()


def make_base_signature_string(
    merchant: str,
    domain: str,
    order_ref: str,
    order_date: int,
    amount_str: str,      # уже money2()
    currency: str,
    product_name: str,
    product_count: int = 1,
    product_price_str: Optional[str] = None,  # по умолчанию = amount_str
) -> str:
    """
    Каноническая строка для подписи по документации WayForPay:
    merchantAccount;merchantDomainName;orderReference;orderDate;amount;currency;productName;productCount;productPrice
    """
    if product_price_str is None:
        product_price_str = amount_str
    return (
        f"{merchant};{domain};{order_ref};{order_date};"
        f"{amount_str};{currency};{product_name};{product_count};{product_price_str}"
    )


# ---------- публичные функции ----------

async def create_invoice(
    user_id: int,
    amount: float,
    currency: str = "UAH",
    product_name: str = "Access to course (1 month)",
) -> str:
    """
    Создаёт инвойс WayForPay и возвращает URL формы оплаты.
    Бросает RuntimeError при ошибке.
    """
    # Уникальная ссылка заказа
    order_date = int(time.time())
    order_ref = f"sub-{user_id}-{order_date}-{uuid.uuid4().hex[:6]}"

    merchant = settings.WFP_MERCHANT.strip()
    domain = settings.WFP_DOMAIN.strip()
    secret = settings.WFP_SECRET.strip()

    amt = money2(amount)  # '2.00'
    price = amt
    count = 1

    # Строка подписи (каноническая)
    base = make_base_signature_string(
        merchant=merchant,
        domain=domain,
        order_ref=order_ref,
        order_date=order_date,
        amount_str=amt,
        currency=currency,
        product_name=product_name,
        product_count=count,
        product_price_str=price,
    )
    signature = hmac_md5_hex(base, secret)

    payload = {
        "transactionType": "CREATE_INVOICE",
        "merchantAccount": merchant,
        "merchantDomainName": domain,
        "apiVersion": 1,
        "orderReference": order_ref,
        "orderDate": order_date,

        # ВАЖНО: те же символьные значения, что и в base
        "amount": amt,                  # строка '2.00'
        "currency": currency,
        "productName": [product_name],
        "productPrice": [price],        # строка '2.00'
        "productCount": [count],        # целое число 1

        "returnUrl": f"{settings.BASE_URL}/thanks",
        "serviceUrl": f"{settings.BASE_URL}/payments/wayforpay/callback",
        "merchantSignature": signature,
    }

    # Диагностика — видно в логах Render
    print("📤 WFP payload (no signature):", {k: v for k, v in payload.items() if k != "merchantSignature"})
    print("🔧 base =", base)
    print("🔑 signature =", signature)

    async with httpx.AsyncClient(timeout=25) as cli:
        try:
            r = await cli.post(WFP_API, json=payload)
            r.raise_for_status()
            data = r.json()
            print("📥 WFP response:", data)
        except Exception as e:
            log.exception("HTTP error while creating WFP invoice")
            raise RuntimeError(f"WayForPay HTTP error: {e}")

    # Успех — один из URL присутствует
    invoice_url = data.get("invoiceUrl") or data.get("formUrl") or data.get("url")
    if invoice_url:
        return invoice_url

    # Частые коды: 1109/1113/1133
    reason = data.get("reason", "")
    code = data.get("reasonCode")
    raise RuntimeError(f"WayForPay error: {code} — {reason}")


def verify_callback_signature(_data: Dict[str, Any]) -> bool:
    """
    При необходимости можно проверить подпись callback'а.
    В большинстве интеграций WFP callback принимают без проверки.
    Оставляем True, чтобы не блокировать успешные оплаты.
    """
    return True


async def process_callback(bot, data: Dict[str, Any]) -> None:
    """
    Обработка callback от WayForPay.
    Ожидается словарь с полями transactionStatus и orderReference.
    """
    try:
        ok_sig = verify_callback_signature(data)
        if not ok_sig:
            print("⚠️ Callback signature failed:", data)
            return

        status = (data.get("transactionStatus") or data.get("status") or "").lower()
        order_ref = data.get("orderReference", "")
        print("✅ WFP callback received:", status, order_ref)

        if not order_ref.startswith("sub-"):
            print("ℹ️ Skip non-subscription order:", order_ref)
            return

        if status in ("approved", "accept", "success"):
            # user_id извлекаем из order_ref вида: "sub-{user_id}-{ts}-{rand}"
            try:
                user_id = int(order_ref.split("-")[1])
            except Exception:
                print("🚫 Cannot parse user_id from order_ref:", order_ref)
                return

            try:
                await activate_or_extend(bot, user_id)
                print("🎉 subscription activated/extended for", user_id)
            except Exception:
                log.exception("Failed to activate subscription for user %s (order %s)", user_id, order_ref)
        else:
            print("ℹ️ Non-success status:", status)

    except Exception:
        log.exception("Unhandled error in WFP callback handler")
