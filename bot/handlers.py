# bot/handlers.py
from __future__ import annotations

from datetime import datetime, timezone
from aiogram import Router, Bot
from aiogram.types import ChatJoinRequest, InlineKeyboardMarkup, InlineKeyboardButton

from .config import settings
from .services import get_subscription_status

router = Router()

def _buy_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💳 Оформити підписку", callback_data="buy_open")]
    ])

@router.chat_join_request()
async def on_chat_join_request(event: ChatJoinRequest, bot: Bot):
    # Обрабатываем только наш канал
    if event.chat.id != settings.CHANNEL_ID:
        return

    user_id = event.from_user.id
    now = datetime.now(timezone.utc)
    sub = await get_subscription_status(user_id)

    is_paid_now = bool(
        sub and sub.status == "active" and sub.paid_until and now <= sub.paid_until
    )

    if is_paid_now:
        try:
            await bot.approve_chat_join_request(settings.CHANNEL_ID, user_id)
        except Exception:
            pass
        return

    # Нет подписки → отклоняем и шлём инструкцию
    try:
        await bot.decline_chat_join_request(settings.CHANNEL_ID, user_id)
    except Exception:
        pass
    try:
        await bot.send_message(
            chat_id=user_id,
            text="❌ Доступ надається лише за активною підпискою.\n\nОформіть підписку і подайте заявку повторно:",
            reply_markup=_buy_kb(),
        )
    except Exception:
        pass
