# bot/handlers_start.py (фрагмент: хендлер кнопки проверки)
from __future__ import annotations
from datetime import datetime, timezone
from aiogram import Router, F, Bot
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from .config import settings
from .services import get_subscription_status, is_member_of_channel

router = Router()

def buy_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="💳 Оформити підписку", callback_data="buy_open")]])

@router.callback_query(F.data == "check_status")
async def on_check_status(cb: CallbackQuery, bot: Bot):
    user_id = cb.from_user.id
    now = datetime.now(timezone.utc)
    sub = await get_subscription_status(user_id)

    def _fmt(dt): return dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC") if dt else "—"

    active = bool(sub and sub.status == "active" and sub.paid_until and now <= sub.paid_until)
    if active:
        await cb.message.answer(f"✅ Підписка активна.\nДоступ до: <b>{_fmt(sub.paid_until)}</b>", parse_mode="HTML")
        await cb.answer(); return

    # фолбек: по факту он внутри канала?
    if await is_member_of_channel(bot, settings.CHANNEL_ID, user_id):
        await cb.message.answer(
            "ℹ️ У вас є доступ до каналу (за фактом членства), але в обліковому записі підписка неактивна. "
            "Якщо ви щойно оплатили — дочекайтесь синхронізації або натисніть «Оформити підписку».",
            reply_markup=buy_kb(),
        )
        await cb.answer(); return

    await cb.message.answer("❌ Підписки немає або вона завершилась.\n\nЩоб отримати доступ — натисніть кнопку нижче 👇", reply_markup=buy_kb())
    await cb.answer()
