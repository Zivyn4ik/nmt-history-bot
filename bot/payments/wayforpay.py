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

# локальная отсечка повторных коллбеков (на процесс)
_processed_refs: set[str] = set()


def _money(x: float | int | str) -> str:
    return str(Decimal(x).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def _sign_string_create_invoice(
    merchant: str,
    domain: str,
    order_ref: str,
    order_date: int,
    amount: str,
    currency: str,
    product_name: str,
    product_count: int,
    product_price: str,
) -> str:
    # сигнатура для CREATE_INVOICE согласно WayForPay:
    # merchantAccount;merchantDomainName;orderReference;orderDate;
    # amount;currency;productName[0];productCount[0];productPrice[0]
    return ";".join(
        [
            merchant,
            domain,
            order_ref,
            str(order_date),
            amount,
            currency,
            product_name,
            str(product_count),
            product_price,
        ]
    )


def _hmac_md5(data: str, secret: str) -> str:
    return hmac.new(secret.encode("utf-8"), data.encode("utf-8"), hashlib.md5).hexdigest()


async def create_invoice(
    *,
    user_id: int,
    amount: float,
    currency: str,
    product_name: str,
) -> str:
    """
    Создаёт инвойс WayForPay и возвращает URL оплаты.
    orderReference формата: sub-<user_id>-<ts>
    """
    merchant = settings.WFP_MERCHANT
    secret = settings.WFP_SECRET
    domain = settings.WFP_DOMAIN

    ts = int(time.time())
    order_ref = f"sub-{user_id}-{ts}"
    amount_str = _money(amount)

    sign_str = _sign_string_create_invoice(
        merchant=merchant,
        domain=domain,
        order_ref=order_ref,
        order_date=ts,
        amount=amount_str,
        currency=currency,
        product_name=product_name,
        product_count=1,
        product_price=amount_str,
    )
    signature = _hmac_md5(sign_str, secret)

    payload: Dict[str, Any] = {
        "transactionType": "CREATE_INVOICE",
        "merchantAccount": merchant,
        "merchantDomainName": domain,
        "merchantSignature": signature,
        "apiVersion": 1,
        "orderReference": order_ref,
        "orderDate": ts,
        "amount": amount_str,
        "currency": currency,
        "productName": [product_name],
        "productPrice": [amount_str],
        "productCount": [1],
        # куда WayForPay будет слать POST-коллбек
        "serviceUrl": settings.BASE_URL.rstrip("/") + "/payments/wayforpay/callback",
        # куда вернуть пользователя после оплаты (по желанию)
        # "returnUrl": settings.BASE_URL.rstrip("/") + "/thanks",
    }

    log.info("WFP create_invoice: %s %s %s", order_ref, amount_str, currency)

    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(WFP_API_URL, json=payload)
        r.raise_for_status()
        data = r.json()

    if data.get("reasonCode") not in (1100, 0, None) and "invoiceUrl" not in data:
        # 1100 — accepted/created, у некоторых аккаунтов reasonCode может отсутствовать
        raise RuntimeError(f"WFP error: {data.get('reason', data)}")

    invoice_url = data.get("invoiceUrl")
    if not invoice_url:
        raise RuntimeError("WFP: invoiceUrl missing in response")

    # можно записать «инициированную оплату» (не обязательно)
    try:
        async with Session() as s:
            exists = await s.execute(select(Payment).where(Payment.order_ref == order_ref))
            if not exists.scalars().first():
                p = Payment(
                    user_id=user_id,
                    order_ref=order_ref,
                    amount=amount_str,
                    currency=currency,
                    status="created",
                )
                s.add(p)
                await s.commit()
    except Exception:
        log.exception("Failed to persist created payment %s", order_ref)

    return invoice_url


def _ok(order_ref: str) -> JSONResponse:
    return JSONResponse(
        {"orderReference": order_ref, "status": "accept", "time": int(time.time())}
    )


def _normalize_status(s: Optional[str]) -> str:
    s = (s or "").strip().lower()
    if s in {"approved", "success", "charged", "completed"}:
        return "approved"
    return s


def _parse_user_id(order_ref: str) -> Optional[int]:
    # sub-<user_id>-<ts>
    try:
        if not order_ref.startswith("sub-"):
            return None
        return int(order_ref.split("-")[1])
    except Exception:
        return None


async def process_callback(request: Request, bot: Bot) -> JSONResponse:
    """
    Обработчик WayForPay callback:
    - при Approved активирует/продлевает подписку,
    - шлёт пользователю «✅ Підписка активна до …»,
    - игнорирует повторные коллбеки для одного orderReference.
    """
    try:
        payload: Dict[str, Any] = await request.json()
    except Exception:
        log.exception("WFP callback: bad JSON")
        return _ok("")

    order_ref = str(payload.get("orderReference") or payload.get("orderReferenceNo") or "")
    status_raw = str(payload.get("transactionStatus") or payload.get("paymentState") or "")
    status = _normalize_status(status_raw)

    # сумма/валюта нам не критичны для активации, но логируем
    amount = payload.get("amount") or payload.get("orderAmount")
    currency = payload.get("currency") or payload.get("orderCurrency")

    log.info("WFP callback received: %s %s", status, order_ref)

    if not order_ref:
        return _ok("")

    if order_ref in _processed_refs:
        log.info("Duplicate callback ignored: %s", order_ref)
        return _ok(order_ref)

    user_id = _parse_user_id(order_ref)
    if user_id is None:
        log.warning("WFP callback: unknown orderReference format: %s", order_ref)
        _processed_refs.add(order_ref)
        return _ok(order_ref)

    if status == "approved":
        try:
            # активируем/продлеваем и отправляем ссылку в канал (join-request)
            await activate_or_extend(bot, user_id)

            # дублирующее подтверждение срока (чтобы было точно видно)
            sub = await get_subscription_status(user_id)
            if sub and sub.paid_until:
                await bot.send_message(
                    user_id,
                    f"✅ Підписка активна до <b>{sub.paid_until.date()}</b>.",
                    parse_mode="HTML",
                )
        except Exception:
            log.exception("WFP: failed to activate/notify for %s", user_id)

        # опционально отмечаем платеж как approved
        try:
            async with Session() as s:
                res = await s.execute(select(Payment).where(Payment.order_ref == order_ref))
                pay = res.scalars().first()
                if pay:
                    pay.status = "approved"
                    await s.commit()
        except Exception:
            pass

        _processed_refs.add(order_ref)
        return _ok(order_ref)

    # прочие финальные статусы
    if status in {"declined", "expired", "voided", "refunded"}:
        try:
            await bot.send_message(
                user_id,
                "❌ Оплата не підтверджена або скасована. Спробуйте ще раз через /buy.",
            )
        except Exception:
            pass
        _processed_refs.add(order_ref)
        return _ok(order_ref)

    # InProcessing и т.п.
    _processed_refs.add(order_ref)
    return _ok(order_ref)
