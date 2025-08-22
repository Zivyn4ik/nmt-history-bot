# payments/wayforpay.py
from __future__ import annotations

import logging
import time
from typing import Any, Dict

from fastapi import Request
from starlette.responses import JSONResponse
from aiogram import Bot

from ..services import activate_or_extend, get_subscription_status
from ..config import settings

log = logging.getLogger("app")

# Память для отсечки повторных коллбеков от WFP (на один процесс)
# Если есть желание, можно хранить orderReference в БД, но для начала этого достаточно.
_processed_refs: set[str] = set()


def _ok(order_ref: str) -> JSONResponse:
    """Ответ, который ожидает WayForPay на callback."""
    return JSONResponse(
        {
            "orderReference": order_ref,
            "status": "accept",
            "time": int(time.time()),
        }
    )


def _normalize_status(s: str | None) -> str:
    s = (s or "").strip().lower()
    # Возможные статусы WFP: Approved, InProcessing, Declined, Expired, Voided, Refund, ChargedBack, etc.
    # Нас интересуют успешные:
    if s in {"approved", "success", "charged", "completed"}:
        return "approved"
    return s


def _parse_user_id(order_ref: str) -> int | None:
    # ожидаем формат sub-<user_id>-<timestamp>
    try:
        if not order_ref.startswith("sub-"):
            return None
        parts = order_ref.split("-")
        return int(parts[1])
    except Exception:
        return None


async def process_callback(request: Request, bot: Bot) -> JSONResponse:
    """
    Обработчик WayForPay callback.
    Важно: сам FastAPI-роут должен вызывать ЭТУ функцию и передать сюда bot.
    """
    try:
        payload: Dict[str, Any] = await request.json()
    except Exception:
        log.exception("WFP callback: bad JSON body")
        return _ok(order_ref="")

    order_ref = str(payload.get("orderReference") or payload.get("orderReferenceNo") or "")
    status_raw = str(payload.get("transactionStatus") or payload.get("paymentState") or "")
    status = _normalize_status(status_raw)

    log.info("WFP callback received: %s %s", status, order_ref)

    # Всегда отвечаем WFP (даже если ничего не делаем), иначе шлюз может ретраить.
    if not order_ref:
        return _ok(order_ref="")

    # Отсекаем повторные коллбеки с тем же orderReference, чтобы не продлевать дважды
    if order_ref in _processed_refs:
        log.info("Duplicate callback ignored: %s", order_ref)
        return _ok(order_ref=order_ref)

    user_id = _parse_user_id(order_ref)
    if user_id is None:
        # Не наша операция — просто подтвердим коллбек
        log.warning("WFP callback: unknown orderReference format: %s", order_ref)
        return _ok(order_ref=order_ref)

    # Ветка успешной оплаты
    if status == "approved":
        try:
            # 1) Продлеваем/активируем подписку и отправляем join-request ссылку
            await activate_or_extend(bot, user_id)

            # 2) Явно сообщим пользователю срок (дублируем, чтобы было точно видно)
            sub = await get_subscription_status(user_id)
            if sub and sub.paid_until:
                await bot.send_message(
                    user_id,
                    f"✅ Підписка активна до <b>{sub.paid_until.date()}</b>.",
                    parse_mode="HTML",
                )
        except Exception:
            log.exception("WFP callback: failed to activate/notify user_id=%s", user_id)

        _processed_refs.add(order_ref)
        return _ok(order_ref=order_ref)

    # Прочие статусы — информируем при желании
    if status in {"declined", "expired", "voided", "refunded"}:
        try:
            await bot.send_message(
                user_id,
                "❌ Оплата не підтверджена або скасована. Спробуйте ще раз через /buy.",
            )
        except Exception:
            pass
        _processed_refs.add(order_ref)
        return _ok(order_ref=order_ref)

    # InProcessing и т.п. — просто подтверждаем, без действий
    _processed_refs.add(order_ref)
    return _ok(order_ref=order_ref)
