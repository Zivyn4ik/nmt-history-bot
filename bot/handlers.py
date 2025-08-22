from __future__ import annotations

from aiogram import Router, F, Bot
from aiogram.filters import CommandStart
from aiogram.types import Message, ChatJoinRequest

from .config import settings
from .services import ensure_user, get_subscription_status, has_active_access

router = Router()

@router.message(CommandStart())
async def cmd_start(message: Message, bot: Bot):
    await ensure_user(message.from_user)

    sub = await get_subscription_status(message.from_user.id)
    if sub.status == "active" and sub.paid_until:
        from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

keyboard_buy = InlineKeyboardMarkup(
    inline_keyboard=[
        [InlineKeyboardButton(text="💳 Оформити підписку", callback_data="buy")]
    ]
)

await message.answer(
    "❌ Підписки немає або вона завершилась.\n\n"
    "Щоб продовжити доступ — натисніть кнопку нижче 👇",
    reply_markup=keyboard_buy
)

    else:
        await message.answer(
            "Підписки немає або вона завершилась. Оформіть оплату через /buy"
        )

@router.chat_join_request(F.chat.id == settings.CHANNEL_ID)
async def on_join_request(event: ChatJoinRequest, bot: Bot):
    uid = event.from_user.id
    if await has_active_access(uid):
        try:
            await bot.approve_chat_join_request(chat_id=settings.CHANNEL_ID, user_id=uid)
        except Exception:
            pass
    else:
        try:
            await bot.send_message(uid, "Щоб увійти до каналу — оформіть підписку через /buy")
        except Exception:
            pass
