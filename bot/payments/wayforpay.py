# bot/payments/wayforpay.py
from __future__ import annotations

import time
import hmac
import hashlib
import logging
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Dict, Optional

import httpx
from fastapi import Request
from starlette.responses import JSONResponse
from aiogram import Bot
from sqlalchemy import select

from ..config import settings
from ..services import activate_or_extend, get_subscription_status
from ..db import Session, Payment

log = logging.getLogger("payments.wayforpay")

WFP_API_URL = "https://api.wayforpay.com/api"
_processed_refs: set[str] = set()

def _money(x: float | int | str) -> str:
    return str(Decimal(x).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))

def _sign_create_invoice(merchant: str, domain: str, order_ref: str, order_date: int,
                         amount: str, currency: str, product_name: str, product_price: str) -> str:
    s = ";".join([merchant, domain, order_ref, str(order_date), amount, currency, product_name, "1", product_price])
    return hmac.new(settings.WFP_SECRET.encode(), s.encode(), hashlib.md5).hexdigest()

async def create_invoice(*, user_id: int, amount: float, currency: str, product_name: str) -> str:
    merchant = settings.WFP_MERCHANT
    domain = settings.WFP_DOMAIN
    ts = int(time.time())
    order_ref = f"sub-{user_id}-{ts}"
    amt = _money(amount)
    sign = _sign_create_invoice(merchant, domain, order_ref, ts, amt, currency, product_name, amt)

    payload: Dict[str, Any] = {
        "transactionType": "CREATE_INVOICE",
        "merchantAccount": merchant,
        "merchantDomainName": domain,
        "merchantSignature": sign,
        "apiVersion": 1,
        "orderReference": order_ref,
        "orderDate": ts,
        "amount": amt,
        "currency": currency,
        "productName": [product_name],
        "productPrice": [amt],
        "productCount": [1],
        "serviceUrl": settings.BASE_URL.rstrip("/") + "/payments/wayforpay/callback",
    }

    log.info("WFP CREATE_INVOICE start: ref=%s amount=%s %s", order_ref, amt, currency)
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(WFP_API_URL, json=payload)
        r.raise_for_status()
        data = r.json()
    log.info("WFP CREATE_INVOICE resp: %s", data)

    invoice_url = data.get("invoiceUrl")
    if not invoice_url:
        raise RuntimeError(f"WFP error: {data}")

    try:
        async with Session() as s:
            exists = await s.execute(select(Payment).where(Payment.order_ref == order_ref))
            if not exists.scalars().first():
                s.add(Payment(user_id=user_id, order_ref=order_ref, amount=amt, currency=currency, status="created"))
                await s.commit()
    except Exception:
        log.exception("WFP: persist created payment failed")

    return invoice_url

def _ok(order_ref: str) -> JSONResponse:
    return JSONResponse({"orderReference": order_ref, "status": "accept", "time": int(time.time())})

def _norm_status(s: Optional[str]) -> str:
    s = (s or "").strip().lower()
    return "approved" if s in {"approved", "success", "charged", "completed"} else s

def _parse_uid(order_ref: str) -> Optional[int]:
    try:
        if not order_ref.startswith("sub-"):
            return None
        return int(order_ref.split("-")[1])
    except Exception:
        return None

async def process_callback(request: Request, bot: Bot) -> JSONResponse:
    try:
        payload: Dict[str, Any] = await request.json()
    except Exception:
        log.exception("WFP callback: bad JSON")
        return _ok("")

    order_ref = str(payload.get("orderReference") or payload.get("orderReferenceNo") or "")
    status_raw = str(payload.get("transactionStatus") or payload.get("paymentState") or "")
    status = _norm_status(status_raw)

    log.info("WFP callback IN: status=%s ref=%s payload=%s", status, order_ref, payload)

    if not order_ref:
        return _ok("")
    if order_ref in _processed_refs:
        log.info("WFP callback DUP: %s", order_ref)
        return _ok(order_ref)

    user_id = _parse_uid(order_ref)
    if user_id is None:
        log.warning("WFP callback: wrong orderReference format: %s", order_ref)
        _processed_refs.add(order_ref)
        return _ok(order_ref)

    if status == "approved":
        try:
            log.info("ACTIVATE start for user_id=%s", user_id)
            await activate_or_extend(bot, user_id)
            sub = await get_subscription_status(user_id)
            log.info("ACTIVATE done user_id=%s status=%s paid_until=%s", user_id, getattr(sub, "status", None), getattr(sub, "paid_until", None))
            if sub and sub.paid_until:
                await bot.send_message(user_id, f"✅ Підписка активна до <b>{sub.paid_until.date()}</b>.", parse_mode="HTML")
        except Exception:
            log.exception("WFP: activate/notify failed for user_id=%s", user_id)

        _processed_refs.add(order_ref)
        return _ok(order_ref)

    if status in {"declined", "expired", "voided", "refunded"}:
        try:
            await bot.send_message(user_id, "❌ Оплата не підтверджена або скасована. Спробуйте ще раз через /buy.")
        except Exception:
            pass
        _processed_refs.add(order_ref)
        return _ok(order_ref)

    _processed_refs.add(order_ref)
    return _ok(order_ref)
