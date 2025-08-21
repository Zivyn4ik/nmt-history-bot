from __future__ import annotations
import time, uuid, hashlib, logging
import httpx
from typing import Dict, Any, List
from ..config import settings
from ..services import activate_or_extend

log = logging.getLogger("bot.payments")
WFP_API = "https://api.wayforpay.com/api"


def md5_hex(s: str) -> str:
    return hashlib.md5(s.encode("utf-8")).hexdigest()


def fmt_amount(x: float) -> str:
    """
    Формат суммы, как обычно ожидают платёжки:
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
    amt = fmt_amount(amount)
    # Каноническая строка из доков WFP:
    # merchantAccount;merchantDomainName;orderReference;orderDate;amount;currency;productName;productCount;productPrice
    base = f"{merchant};{domain};{order_ref};{order_date};{amt};{currency};{product_name};1;{amt}"
    return [base]


def make_sign_candidates(base: str, secret: str) -> List[str]:
    # Каноничная формула подписи
    return [md5_hex(base.strip() + secret.strip())]


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

    # Отладка входных данных
    print("👁 Отправка данных в WayForPay:")
    print("merchant =", merchant)
    print("domain   =", domain)
    print("order_ref=", order_ref)
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
        "amount": float(fmt_amount(amount)),  # само поле можно отправлять числом
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

                reason = (data.get("reason") or data.get("message") or "").lower()
                invoice_url = data.get("invoiceUrl")
                print("📦 WFP response: ok =", bool(invoice_url), "reason =", reason, "data:", data)

                if invoice_url:
                    return invoice_url

                # Если причина не связана с подписью — выбрасываем, чтобы не скрыть реальную ошибку
                if "signature" not in reason and data.get("reasonCode") not in (1109, 1133):
                    raise RuntimeError(f"WayForPay error: {data}")

    raise RuntimeError(
        "WayForPay error: Invalid signature for all known formulas. "
        "Проверьте merchant/domain/secret; если верны — скажите, добавлю ещё формулу."
    )


def verify_callback_signature(data: Dict[str, Any]) -> bool:
    # TODO: при необходимости добавить валидацию подписи колбэка
    return True


async def process_callback(bot, data: Dict[str, Any]) -> None:
    if not verify_callback_signature(data):
        print("⚠️ Callback signature failed:", data)
        return

    status = (data.get("transactionStatus") or data.get("status") or "").lower()
    order_ref = data.get("orderReference", "")
    print("✅ WFP callback received:", status, order_ref)

    if status in ("approved", "accept", "success") and order_ref.startswith("sub-"):
        try:
            user_id = int(order_ref.split("-")[1])
        except Exception:
            print("🚫 Cannot parse user_id from order_ref:", order_ref)
            return
        await activate_or_extend(bot, user_id)
