from __future__ import annotations

from aiogram import Router, F, Bot
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton

from bot.config import settings
from bot.services import get_subscription_status, is_member_of_channel, ensure_user
from bot.handlers_buy import cmd_buy

router = Router()

# --- keyboards ---
def buy_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="💳 Оформити підписку", callback_data="buy")]]
    )

# --- callbacks ---
@router.callback_query(F.data == "check_status")
async def on_check_status(cb: CallbackQuery, bot: Bot):
    await cb.answer()
    user_id = cb.from_user.id
    await ensure_user(cb.from_user)
    sub = await get_subscription_status(user_id)

    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    paid_until = sub.paid_until
    status = sub.status
    active_by_db = sub is not None and status == "active" and paid_until and now <= paid_until

    in_channel = await is_member_of_channel(bot, settings.CHANNEL_ID, user_id)

    if active_by_db:
        text = f"✅ Підписка активна.\nДоступ до каналу надано до: <b>{paid_until.strftime('%Y-%m-%d %H:%M UTC')}</b>"
        await cb.message.answer(text, parse_mode="HTML")
        return

    if in_channel:
        text = (
            "ℹ️ Ви зараз маєте доступ до каналу (за фактом членства), "
            "але в обліковому записі підписка виглядає неактивною.\n\n"
            "Якщо ви нещодавно оплатили — зачекайте кілька хвилин, або натисніть «Оформити підписку», "
            "щоб повторно синхронізувати платіж."
        )
        await cb.message.answer(text, reply_markup=buy_kb())
        return

    await cb.message.answer(
        "❌ Підписки немає або вона завершилась.\n\n"
        "Щоб отримати доступ — натисніть кнопку нижче 👇",
        reply_markup=buy_kb(),
    )

@router.callback_query(F.data == "buy")
async def cb_buy(call: CallbackQuery, bot: Bot):
    await cmd_buy(call.message, bot)
