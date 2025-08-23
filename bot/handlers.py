# handlers_start.py
from __future__ import annotations

from datetime import datetime, timezone
from aiogram import Router, F, Bot
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton

from .config import settings
from .services import get_subscription_status, is_member_of_channel

router = Router()

def buy_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="💳 Оформити підписку", callback_data="buy_open")],
        ]
    )

@router.callback_query(F.data == "check_status")
async def on_check_status(cb: CallbackQuery, bot: Bot):
    """Перевірка статусу підписки з урахуванням TZ і фолбеком на реальне членство в каналі."""
    user_id = cb.from_user.id
    now = datetime.now(timezone.utc)

    sub = await get_subscription_status(user_id)

    def _tz_aware(dt):
        if dt is None:
            return None
        # Робимо paid_until timezone-aware (UTC), якщо воно naive
        return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)

    paid_until = _tz_aware(getattr(sub, "paid_until", None))
    status = getattr(sub, "status", None)

    active_by_db = bool(
        sub is not None
        and status == "active"
        and paid_until is not None
        and now <= paid_until
    )

    # Фолбек: якщо за БД неактивний, але фактично у каналі — покажемо, що доступ є,
    # і запропонуємо синхронізувати/поновити підписку.
    in_channel = await is_member_of_channel(bot, settings.CHANNEL_ID, user_id)

    if active_by_db:
        text = "✅ Підписка активна.\nДоступ до каналу надано до: <b>{}</b>".format(
            paid_until.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        )
        await cb.message.answer(text, parse_mode="HTML")
        await cb.answer()
        return

    if in_channel:
        # Користувач у каналі, але БД каже «неактивно» → ймовірно проблема з TZ/синхронізацією
        text = (
            "ℹ️ Ви зараз маєте доступ до каналу (за фактом членства), "
            "але в обліковому записі підписка виглядає неактивною.\n\n"
            "Якщо ви нещодавно оплатили — зачекайте кілька хвилин, або натисніть «Оформити підписку», "
            "щоб повторно синхронізувати платіж."
        )
        await cb.message.answer(text, reply_markup=buy_kb())
        await cb.answer()
        return

    # Немає активної підписки і немає членства
    await cb.message.answer(
        "❌ Підписки немає або вона завершилась.\n\n"
        "Щоб отримати доступ — натисніть кнопку нижче 👇",
        reply_markup=buy_kb(),
    )
    await cb.answer()
