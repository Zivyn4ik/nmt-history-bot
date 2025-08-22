# bot/handlers_start.py
from __future__ import annotations

from datetime import datetime, timezone
from aiogram import Router, F, Bot
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton

from .config import settings
from .services import get_subscription_status, is_member_of_channel, ensure_user

router = Router()

def main_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💳 Оформити підписку", callback_data="buy_open")],
        [InlineKeyboardButton(text="✅ Перевірка статусу підписки", callback_data="check_status")],
    ])

@router.message(F.text == "/start")
async def cmd_start(message: Message):
    await ensure_user(message.from_user)
    await message.answer("Ласкаво просимо! Оберіть дію:", reply_markup=main_kb())

def buy_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💳 Оформити підписку", callback_data="buy_open")]
    ])

@router.callback_query(F.data == "check_status")
async def on_check_status(cb: CallbackQuery, bot: Bot):
    user_id = cb.from_user.id
    now = datetime.now(timezone.utc)
    sub = await get_subscription_status(user_id)

    def _fmt(dt):
        return dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC") if dt else "—"

    active = bool(sub and sub.status == "active" and sub.paid_until and now <= sub.paid_until)
    if active:
        await cb.message.answer(f"✅ Підписка активна.\nДоступ до: <b>{_fmt(sub.paid_until)}</b>", parse_mode="HTML")
        await cb.answer()
        return

    # Фолбек: фактически уже в канале?
    if await is_member_of_channel(bot, settings.CHANNEL_ID, user_id):
        await cb.message.answer(
            "ℹ️ Ви маєте доступ до каналу за фактом членства, але в обліковому записі підписка неактивна. "
            "Якщо ви щойно оплатили — дочекайтесь синхронізації або натисніть «Оформити підписку».",
            reply_markup=buy_kb(),
        )
        await cb.answer()
        return

    await cb.message.answer(
        "❌ Підписки немає або вона завершилась.\n\nЩоб отримати доступ — натисніть кнопку нижче 👇",
        reply_markup=buy_kb(),
    )
    await cb.answer()
