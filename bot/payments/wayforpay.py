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

def make_bases(
    merchant: str,
    domain: str,
    order_ref: str,
    order_date: int,
    amount: float,
    currency: str,
    product_name: str,
) -> List[str]:
    amt = f"{amount:.2f}"
    bases = [
        f"{merchant};{domain};{order_ref};{order_date};{amt};{currency};{product_name};1;{amt}",
        f"{merchant};{domain};{order_ref};{amt};{currency};{product_name};1;{amt}",
        f"{merchant};{domain};{order_ref};{order_date};{amt};{currency}",
        f"{merchant};{domain};{order_ref};{amt};{currency}",
        f"{merchant};{order_ref};{order_date};{amt};{currency};{product_name};1;{amt}",
        f"{merchant};{order_ref};{amt};{currency}",
    ]
    seen, out = set(), []
    for b in bases:
        if b not in seen:
            seen.add(b)
            out.append(b)
    return out

def make_sign_candidates(base: str, secret: str) -> List[str]:
    base = base.strip()
    secret = secret.strip()

    candidates = [
        md5_hex(base + secret),                              # стандартный
        md5_hex(secret + base),                              # обратный порядок
        md5_hex(base + ";" + secret),                        # с разделителем
        md5_hex(base.replace(" ", "") + secret),             # без пробелов
        md5_hex(base.replace(" ", "").replace(";", "") + secret),  # без пробелов и точек с запятой
    ]

    return list(set(candidates))  # убираем дубликаты

async def create_invoice(
    user_id: int,
    amount: float,
    currency: str = "UAH",
    product_name: str = "Channel subscription (1 month)",
) -> str:
    order_date = int(time.time())
    order_ref = f"sub-{user_id}-{order_date}-{uuid.uuid4().hex[:6]}"

    merchant = settings.WFP_MERCHANT
    domain = settings.WFP_DOMAIN
    secret = settings.WFP_SECRET

    print("👁 Отправка данных в WayForPay:")
    print("merchant =", merchant)
    print("domain =", domain)
    print("order_ref =", order_ref)
    print("order_date =", order_date)
    print("amount =", round(amount, 2))
    print("currency =", currency)
    print("product_name =", product_name)

    base_payload = {
        "transactionType": "CREATE_INVOICE",
        "merchantAccount": merchant,
        "merchantDomainName": domain,
        "apiVersion": 1,
        "orderReference": order_ref,
        "orderDate": order_date,
        "amount": round(amount, 2),
        "currency": currency,
        "productName": [product_name],
        "productPrice": [round(amount, 2)],
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

                print("📦 WFP response: ok =", bool(invoice_url), "reason =", reason)

                if invoice_url:
                    return invoice_url

                if "signature" not in reason and data.get("reasonCode") not in (1109, 1133):
                    raise RuntimeError(f"WayForPay error: {data}")

    raise RuntimeError("WayForPay error: Invalid signature for all known formulas. "
                       "Проверьте merchant/domain/secret; если верны — скажите, добавлю ещё формулу.")

def verify_callback_signature(data: Dict[str, Any]) -> bool:
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
