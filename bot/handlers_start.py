from __future__ import annotations

from aiogram import Router, F, Bot
from aiogram.filters import CommandStart
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from sqlalchemy import select

from bot.config import settings
from bot.services import ensure_user, activate_or_extend
from bot.handlers_buy import cmd_buy
from bot.db import Session, PaymentToken

import asyncio

router = Router()

# --- Keyboards ---
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

# --- Polling функции ---
async def wait_for_payment_and_activate(bot: Bot, user_id: int, token: str, timeout: int = 35):
    """
    Проверяет каждые секунду статус оплаты токена.
    При успешной оплате активирует подписку и отправляет приглашение на канал.
    """
    message = await bot.send_message(user_id, "⏳ Генерируем приглашение…")

    start_time = asyncio.get_event_loop().time()
    while asyncio.get_event_loop().time() - start_time < timeout:
        async with Session() as s:
            res = await s.execute(select(PaymentToken).where(PaymentToken.token == token))
            token_obj = res.scalar_one_or_none()

            if token_obj and token_obj.status == "paid":
                token_obj.used = True
                await s.commit()

                # Активируем подписку
                await activate_or_extend(bot, user_id)

                await message.delete()
                return True
        await asyncio.sleep(1)

    await message.edit_text("❌ Оплата не подтвердилась за 35 секунд. Попробуйте позже.")
    return False

# --- /start ---
@router.message(CommandStart())
async def start_handler(message: Message, bot: Bot):
    user = message.from_user
    await ensure_user(user)

    # Проверяем, пришёл ли токен через start parameter
    token = getattr(message, "start_param", None)
    if token:
        # Запускаем проверку оплаты через polling
        asyncio.create_task(wait_for_payment_and_activate(bot, user.id, token))
        await message.answer(
            "🟢 Вы успешно перешли в бота после оплаты. "
            "Проверяем статус оплаты и готовим приглашение в канал…"
        )

    text = (
        "👋 <b>Вітаємо у навчальному боті HMT 2026 | Історія України!</b>\n\n"
        "📚 Тут ви отримаєте доступ до:\n"
        "• Таблиць для підготовки до НМТ\n"
        "• Тестів та завдань з поясненнями\n"
        "• Корисних матеріалів від викладачів\n\n"
        "🧭 Скористайтесь кнопками нижче."
    )
    await message.answer(text, reply_markup=_main_menu_kb())

# --- Callbacks ---
@router.callback_query(F.data == "buy")
async def cb_buy(call: CallbackQuery, bot: Bot):
    await cmd_buy(call.message, bot)

@router.callback_query(F.data == "check_status")
async def cb_check(call: CallbackQuery, bot: Bot):
    await call.answer()
    user = call.from_user
    await ensure_user(user)

    from bot.services import get_subscription_status
    sub = await get_subscription_status(user.id)

    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)

    if sub.status == "active" and sub.paid_until and now <= sub.paid_until:
        remaining = sub.paid_until - now
        days_left = remaining.days
        hours_left = remaining.seconds // 3600
        minutes_left = (remaining.seconds % 3600) // 60
        text = f"✅ Підписка активна. Залишилось: {days_left}д {hours_left}г {minutes_left}хв."
        invite = getattr(settings, "TG_JOIN_REQUEST_URL", "")
        if invite:
            text += f"\nЩоб увійти в канал, перейдіть за посиланням:\n{invite}"
        await call.message.answer(text)
    else:
        await call.message.answer(
            "❌ Підписки немає або вона завершилась.\n\n"
            "Щоб отримати доступ — натисніть кнопку нижче 👇",
            reply_markup=_buy_kb(),
        )
