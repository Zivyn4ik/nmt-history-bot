from __future__ import annotations

from aiogram import Router, F, Bot
from aiogram.filters import CommandStart
from aiogram.types import (
    Message,
    ChatJoinRequest,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)

from .config import settings
from .services import ensure_user, get_subscription_status, has_active_access

router = Router()

def _buy_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="💳 Оформити підписку", callback_data="buy")]]
    )

@router.message(CommandStart())
async def cmd_start(message: Message, bot: Bot):
    """Показываем статус подписки. Если нет доступа — красивая кнопка вместо /buy."""
    await ensure_user(message.from_user)

    sub = await get_subscription_status(message.from_user.id)

    if getattr(sub, "status", None) == "active" and getattr(sub, "paid_until", None):
        invite = getattr(settings, "TG_JOIN_REQUEST_URL", "")
        text = f"✅ Підписка активна до <b>{sub.paid_until.date()}</b>."
        if invite:
            text += f"\nЯкщо ви ще не в каналі — перейдіть за посиланням:\n{invite}"
        await message.answer(text)
    else:
        await message.answer(
            "❌ Підписки немає або вона завершилась.\n\n"
            "Щоб отримати доступ — натисніть кнопку нижче 👇",
            reply_markup=_buy_kb(),
        )

@router.chat_join_request(F.chat.id == settings.CHANNEL_ID)
async def on_join_request(event: ChatJoinRequest, bot: Bot):
    """Если у пользователя есть доступ — апрувим. Иначе даём кнопку покупки."""
    uid = event.from_user.id
    if await has_active_access(uid):
        try:
            await bot.approve_chat_join_request(chat_id=settings.CHANNEL_ID, user_id=uid)
        except Exception:
            pass
    else:
        try:
            await bot.send_message(
                uid,
                "Щоб увійти до каналу — оформіть підписку:",
                reply_markup=_buy_kb(),
            )
        except Exception:
            pass
