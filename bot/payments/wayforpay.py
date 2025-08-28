import time
import hmac
import hashlib
from typing import Tuple, Dict, Any
import aiohttp
from bot.config import settings

def _sign_create_invoice(payload: Dict[str, Any]) -> str:
    parts = [
        payload["merchantAccount"],
        payload["merchantDomainName"],
        payload["orderReference"],
        str(payload["orderDate"]),
        str(payload["amount"]),
        payload["currency"],
    ]
    parts.extend(payload["productName"])
    parts.extend(str(x) for x in payload["productCount"])
    parts.extend(str(x) for x in payload["productPrice"])
    data = ";".join(parts)
    return hmac.new(settings.WFP_SECRET.encode(), data.encode(), hashlib.md5).hexdigest()

def _sign_check_status(merchant: str, order_ref: str) -> str:
    data = f"{merchant};{order_ref}"
    return hmac.new(settings.WFP_SECRET.encode(), data.encode(), hashlib.md5).hexdigest()

async def create_invoice(user_id: int) -> Tuple[str, str]:
    now_ts = int(time.time())
    order_reference = f"{user_id}-{now_ts}"
    payload = {
        "transactionType": "CREATE_INVOICE",
        "merchantAccount": settings.WFP_MERCHANT,
        "merchantDomainName": settings.WFP_DOMAIN,
        "apiVersion": 1,
        "language": "UA",
        "orderReference": order_reference,
        "orderDate": now_ts,
        "amount": settings.PRICE,
        "currency": settings.CURRENCY,
        "productName": [settings.PRODUCT_NAME],
        "productPrice": [settings.PRICE],
        "productCount": [1],
        "returnUrl": settings.return_url,
        "clientAccountId": str(user_id),
    }
    payload["merchantSignature"] = _sign_create_invoice(payload)

    async with aiohttp.ClientSession() as session:
        async with session.post(settings.service_url, json=payload, timeout=30) as resp:
            data = await resp.json()
            if data.get("reasonCode") not in (1100, 1108):
                raise RuntimeError(f"WFP create_invoice error: {data}")
            return order_reference, data["invoiceUrl"]

async def check_status(order_reference: str) -> Dict[str, Any]:
    payload = {
        "transactionType": "CHECK_STATUS",
        "merchantAccount": settings.WFP_MERCHANT,
        "orderReference": order_reference,
        "merchantSignature": _sign_check_status(settings.WFP_MERCHANT, order_reference)
    }
    async with aiohttp.ClientSession() as session:
        async with session.post(settings.service_url, json=payload, timeout=20) as resp:
            return await resp.json()
