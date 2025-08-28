from __future__ import annotations

from aiogram import Router, F, Bot
from aiogram.filters import CommandStart
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton
from sqlalchemy import select
import asyncio
from datetime import datetime, timezone

from bot.config import settings
from bot.services import ensure_user, get_subscription_status, activate_or_extend
from bot.db import Session, PaymentToken

router = Router()


def _main_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💳 Оформити підписку", callback_data="buy")],
        [InlineKeyboardButton(text="✅ Перевірка статусу підписки", callback_data="check_status")],
        [InlineKeyboardButton(text="💬 Підтримка", url="https://t.me/zivyn4ik")],
    ])


def _buy_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💳 Оформити підписку", callback_data="buy")]
    ])


@router.message(CommandStart())
async def start_handler(message: Message, bot: Bot):
    user = message.from_user
    await ensure_user(user)

    token = getattr(message, "start_param", None)
    if token:
        # Сначала проверяем оплату и выдаём invite
        await message.answer("⏳ Перевіряємо статус оплати…")
        asyncio.create_task(check_payment_and_send_invite(bot, user.id, token))

    text = (
        "👋 <b>Вітаємо у навчальному боті HMT 2026 | Історія України!</b>\n\n"
        "📚 Тут ви отримаєте доступ до:\n"
        "• Таблиць для підготовки до НМТ\n"
        "• Тестів та завдань з поясненнями\n"
        "• Корисних матеріалів від викладачів\n\n"
        "🧭 Скористайтесь кнопками нижче."
    )
    await message.answer(text, reply_markup=_main_menu_kb())


async def check_payment_and_send_invite(bot: Bot, user_id: int, token: str, timeout: int = 35):
    start_time = datetime.utcnow()
    try:
        message = await bot.send_message(user_id, "⏳ Перевіряємо статус оплати…")
    except Exception:
        message = None

    while (datetime.utcnow() - start_time).total_seconds() < timeout:
        async with Session() as s:
            res = await s.execute(
                select(PaymentToken).where(PaymentToken.token == token)
            )
            token_obj = res.scalar_one_or_none()
            if token_obj and token_obj.status == "paid":
                token_obj.used = True
                await s.commit()
                if message:
                    await message.delete()
                await activate_or_extend(bot, user_id)
                return
        await asyncio.sleep(1)

    if message:
        await message.edit_text("❌ Оплата не підтвердилась за 35 секунд. Спробуйте ще раз пізніше.")


@router.callback_query(F.data == "buy")
async def cb_buy(call, bot: Bot):
    from bot.handlers_buy import cmd_buy
    await cmd_buy(call.message, bot)


@router.callback_query(F.data == "check_status")
async def cb_check(call):
    await call.answer()
    user = call.from_user
    await ensure_user(user)

    sub = await get_subscription_status(user.id)
    now = datetime.now(timezone.utc)

    if sub.status == "active" and sub.paid_until and now <= sub.paid_until:
        remaining = sub.paid_until - now
        days, seconds = remaining.days, remaining.seconds
        hours = seconds // 3600
        minutes = (seconds % 3600) // 60
        await call.message.answer(
            f"✅ Підписка активна.\nЗалишилось: {days}д {hours}г {minutes}хв."
        )
    else:
        await call.message.answer(
            "❌ Підписки немає або вона завершилась.\nЩоб отримати доступ — натисніть кнопку нижче 👇",
            reply_markup=_buy_kb(),
        )
