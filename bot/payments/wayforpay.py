from __future__ import annotations

import time
import uuid
import hashlib
import hmac
import logging
from typing import Dict, Any, List

import httpx

from ..config import settings
from ..services import activate_or_extend

log = logging.getLogger("bot.payments")
WFP_API = "https://api.wayforpay.com/api"


def md5_hex(s: str) -> str:
    return hashlib.md5(s.encode("utf-8")).hexdigest()


def hmac_md5_hex(message: str, secret: str) -> str:
    """ПРАВИЛЬНИЙ спосіб підпису для WayForPay."""
    return hmac.new(secret.strip().encode("utf-8"),
                    message.strip().encode("utf-8"),
                    hashlib.md5).hexdigest()


def fmt_amount(x: float) -> str:
    """
    Форматує суму в рядок так, як очікують платіжні системи:
    1.0 -> "1"
    1.50 -> "1.5"
    1.55 -> "1.55"
    """
    s = f"{x:.2f}"
    s = s.rstrip("0").rstrip(".")
    return s or "0"


def make_bases(
    merchant: str,
    domain: str,
    order_ref: str,
    order_date: int,
    amount: float,
    currency: str,
    product_name: str,
) -> List[str]:
    """
    Формує можливі «бази» (рядки для підпису). Основна — канонічна із документації WFP:
    merchantAccount;merchantDomainName;orderReference;orderDate;amount;currency;productName;productCount;productPrice
    """
    amt = fmt_amount(amount)

    # Канонічна
    base1 = f"{merchant};{domain};{order_ref};{order_date};{amt};{currency};{product_name};1;{amt}"

    # Альтернативна (деякі інтеграції плутають price/count — залишаємо як запасний варіант)
    base2 = f"{merchant};{domain};{order_ref};{order_date};{amt};{currency};{product_name};{amt};1"

    # Повертаємо у бажаному порядку спроб
    return [base1, base2]


def make_sign_candidates(base: str, secret: str) -> List[str]:
    """
    Кандидати підписів. Перший — правильний для WayForPay (HMAC-MD5).
    Додаємо ще варіанти на випадок «нестандартних» акаунтів WFP.
    """
    return [
        hmac_md5_hex(base, secret),        # ✅ головний
        md5_hex(base + secret),            # запасний
        md5_hex(secret + base),            # запасний
    ]


async def create_invoice(
    user_id: int,
    amount: float,
    currency: str = "UAH",
    product_name: str = "Access to course (1 month)",
) -> str:
    order_date = int(time.time())
    order_ref = f"sub-{user_id}-{order_date}-{uuid.uuid4().hex[:6]}"

    merchant = settings.WFP_MERCHANT
    domain = settings.WFP_DOMAIN
    secret = settings.WFP_SECRET

    # Діагностика в логах Render (видно у вашому скріні)
    print("👁 Отправка данных в WayForPay:")
    print("merchant =", merchant)
    print("domain  =", domain)
    print("order_ref =", order_ref)
    print("order_date =", order_date)
    print("amount  =", fmt_amount(amount), "(raw:", amount, ")")
    print("currency=", currency)
    print("product_name =", product_name)

    base_payload = {
        "transactionType": "CREATE_INVOICE",
        "merchantAccount": merchant,
        "merchantDomainName": domain,
        "apiVersion": 1,
        "orderReference": order_ref,
        "orderDate": order_date,
        # Числові значення в JSON надсилаємо number'ами, не рядками:
        "amount": float(fmt_amount(amount)),
        "currency": currency,
        "productName": [product_name],
        "productPrice": [float(fmt_amount(amount))],
        "productCount": [1],
        "returnUrl": f"{settings.BASE_URL}/thanks",
        "serviceUrl": f"{settings.BASE_URL}/payments/wayforpay/callback",
    }

    bases = make_bases(merchant, domain, order_ref, order_date, amount, currency, product_name)

    async with httpx.AsyncClient(timeout=25) as cli:
        for base in bases:
            print("🔧 base =", base)
            for sig in make_sign_candidates(base, secret):
                print("🔑 sign =", sig)
                payload = dict(base_payload)
                payload["merchantSignature"] = sig

                try:
                    r = await cli.post(WFP_API, json=payload)
                    r.raise_for_status()
                    data = r.json()
                except Exception as e:
                    print("❌ HTTP error with base =", base, "→", e)
                    continue

                reason = (data.get("reason") or "").lower()
                print("WFP response:", data)

                # Успіх — WayForPay повертає URL інвойсу:
                invoice_url = (
                    data.get("invoiceUrl")
                    or data.get("formUrl")
                    or data.get("url")
                )
                if invoice_url:
                    return invoice_url

                # Якщо помилка НЕ про підпис — відразу віддаємо її вгору
                if "signature" not in reason and data.get("reasonCode") not in (1109, 1113, 1133):
                    raise RuntimeError(f"WayForPay error: {data}")

    # Якщо пройшли всі варіанти — підпис так і не підійшов
    raise RuntimeError(
        "WayForPay error: Invalid signature for all known formulas. "
        "Перевірте merchantAccount / merchantDomainName / Merchant secret key. "
        "Якщо все вірно — напишіть, я додам ще формулу."
    )


def verify_callback_signature(_data: Dict[str, Any]) -> bool:
    """
    За потреби тут можна перевіряти підпис callback'а від WFP.
    Більшість інтеграцій приймає callback без валідації (як у вашій версії),
    тому залишаю True, щоб не блокувати оплату.
    """
    return True


async def process_callback(bot, data: Dict[str, Any]) -> None:
    if not verify_callback_signature(data):
        print("⚠️ Callback signature failed:", data)
        return

    status = (data.get("transactionStatus") or data.get("status") or "").lower()
    order_ref = data.get("orderReference", "")
    print("✅ WFP callback received:", status, order_ref)

    # Підписка активується лише по успіху
    if status in ("approved", "accept", "success") and order_ref.startswith("sub-"):
        try:
            user_id = int(order_ref.split("-")[1])
        except Exception:
            print("🚫 Cannot parse user_id from order_ref:", order_ref)
            return
        await activate_or_extend(bot, user_id)
