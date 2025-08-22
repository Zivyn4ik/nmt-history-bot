# bot/handlers.py
from __future__ import annotations

from datetime import datetime, timezone

from aiogram import Router, Bot
from aiogram.types import ChatJoinRequest, InlineKeyboardMarkup, InlineKeyboardButton

from .config import settings
from .services import get_subscription_status

router = Router()

def _buy_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Оплатити підписку", callback_data="buy_open")],
        ]
    )

@router.chat_join_request()
async def on_chat_join_request(event: ChatJoinRequest, bot: Bot):
    """
    Приймаємо join-request лише від користувачів із дійсною (оплаченою на зараз) підпискою.
    Усі інші — відхиляємо та надсилаємо інструкцію на оплату в особисті повідомлення.
    """
    # 1) Ігноруємо заявки не в наш канал
    if event.chat.id != settings.CHANNEL_ID:
        return

    user_id = event.from_user.id
    now = datetime.now(timezone.utc)

    # 2) Перевірка статусу підписки
    sub = await get_subscription_status(user_id)

    is_paid_now = (
        sub is not None
        and getattr(sub, "status", None) == "active"
        and getattr(sub, "paid_until", None) is not None
        and now <= sub.paid_until  # доступ дійсний на момент заявки
    )

    if is_paid_now:
        # 3) Дозволяємо вступ
        try:
            await bot.approve_chat_join_request(chat_id=settings.CHANNEL_ID, user_id=user_id)
        except Exception:
            # Мовчазно ігноруємо телеграм-помилки схвалення
            pass
        return

    # 4) Відхиляємо заявку і надсилаємо інструкцію на оплату
    try:
        await bot.decline_chat_join_request(chat_id=settings.CHANNEL_ID, user_id=user_id)
    except Exception:
        pass

    try:
        await bot.send_message(
            chat_id=user_id,
            text=(
                "❌ Доступ до каналу надається лише за активною підпискою.\n\n"
                "Оформіть підписку на 1 місяць — і одразу подавайте заявку ще раз:"
            ),
            reply_markup=_buy_kb(),
        )
    except Exception:
        # Якщо користувач не відкривав діалог із ботом — Telegram може не дозволити написати першим.
        pass
