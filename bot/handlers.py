from __future__ import annotations

from aiogram import Router, F, Bot
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram import types

from bot.config import settings
from bot.services import get_subscription_status, is_member_of_channel, ensure_user
from bot.handlers_buy import cmd_buy  # вызываем функцию cmd_buy из handlers_buy

router = Router()

# --- keyboards ---------------------------------------------------------------

def buy_kb() -> InlineKeyboardMarkup:
    """Кнопка для оформления подписки."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="💳 Оформити підписку", callback_data="buy")],
        ]
    )

# --- callbacks ---------------------------------------------------------------

@router.callback_query(F.data == "check_status")
async def on_check_status(cb: CallbackQuery, bot: Bot):
    """Перевірка статусу підписки з урахуванням TZ і фолбеком на реальне членство в каналі."""
    await cb.answer()
    user_id = cb.from_user.id

    # гарантируем, что user в базе есть (обновим username если нужно)
    await ensure_user(cb.from_user)

    sub = await get_subscription_status(user_id)

    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)

    def _tz_aware(dt):
        if dt is None:
            return None
        return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)

    paid_until = _tz_aware(getattr(sub, "paid_until", None))
    status = getattr(sub, "status", None)

    active_by_db = bool(
        sub is not None
        and status == "active"
        and paid_until is not None
        and now <= paid_until
    )

    in_channel = await is_member_of_channel(bot, settings.CHANNEL_ID, user_id)

    if active_by_db:
        text = "✅ Підписка активна.\nДоступ до каналу надано до: <b>{}</b>".format(
            paid_until.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        )
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
    """
    При нажатии по inline-кнопке 'buy' запускаем тот же flow, что и /buy.
    Передаём в cmd_buy Message и Bot.
    """
    # cmd_buy ожидает Message и Bot
    await cmd_buy(call.message, bot)
