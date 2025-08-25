from __future__ import annotations

from datetime import datetime, timezone
from aiogram import Router, F, Bot
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton

from bot.config import settings
from bot.services import get_subscription_status, is_member_of_channel

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
    user_id = cb.from_user.id
    now = datetime.now(timezone.utc)

    sub = await get_subscription_status(user_id)

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
        await cb.answer()
        return

    if in_channel:
        text = (
            "ℹ️ Ви зараз маєте доступ до каналу (за фактом членства), "
            "але в обліковому записі підписка виглядає неактивною.\n\n"
            "Якщо ви нещодавно оплатили — зачекайте кілька хвилин, або натисніть «Оформити підписку», "
            "щоб повторно синхронізувати платіж."
        )
        await cb.message.answer(text, reply_markup=buy_kb())
        await cb.answer()
        return

    await cb.message.answer(
        "❌ Підписки немає або вона завершилась.\n\n"
        "Щоб отримати доступ — натисніть кнопку нижче 👇",
        reply_markup=buy_kb(),
    )
    await cb.answer()

# --- обработчик кнопки "Оформити підписку" -----------------------------------

@router.callback_query(F.data == "buy")
async def on_buy_subscription(cb: CallbackQuery):
    """Обработка кнопки покупки/подписки."""
    await cb.answer()
    await cb.message.answer(
        "💳 Для оформлення підписки перейдіть за посиланням:\n"
        f"{getattr(settings, 'PAYMENT_URL', 'https://example.com/pay')}"
    )
