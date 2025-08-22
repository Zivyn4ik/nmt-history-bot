# bot/handlers_start.py
from __future__ import annotations

import logging
from aiogram import Router, F, Bot
from aiogram.filters import CommandStart
from aiogram.types import (
    Message,
    CallbackQuery,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)

from .config import settings
from .payments.wayforpay import create_invoice

router = Router()
log = logging.getLogger("handlers.start")


def _start_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="💳 Оформити підписку", callback_data="buy")],
            [InlineKeyboardButton(text="✅ Перевірка статусу підписки", callback_data="check")],
            [InlineKeyboardButton(text="💬 Підтримка", url="https://t.me/zivyn4ik")],
        ]
    )


@router.message(CommandStart())
async def start_handler(message: Message):
    text = (
        "👋 <b>Вітаємо у навчальному боті HMT 2026 | Історія України!</b>\n\n"
        "📚 Тут ви отримаєте доступ до:\n"
        "• Таблиць для підготовки до НМТ\n"
        "• Тестів та завдань з поясненнями\n"
        "• Корисних матеріалів від викладачів\n\n"
        "Щоб продовжити — виберіть дію нижче:"
    )
    await message.answer(text, reply_markup=_start_keyboard())


@router.callback_query(F.data == "buy")
async def cb_buy(call: CallbackQuery, bot: Bot):
    await call.answer()  # ⚠️ Telegram требует ответ в течение 10 секунд

    user_id = call.from_user.id
    try:
        url = await create_invoice(
            user_id=user_id,
            amount=settings.PRICE,
            currency=settings.CURRENCY,
            product_name=getattr(settings, "PRODUCT_NAME", "Channel subscription (1 month)"),
        )
        await bot.send_message(
            chat_id=call.message.chat.id,
            text="🧾 Щоб оплатити підписку, натисніть кнопку нижче:",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text="💳 Оплатити", url=url)],
                ]
            ),
        )
    except Exception as e:
        log.exception("Помилка при створенні інвойсу для користувача %s: %s", user_id, e)
        await bot.send_message(
            chat_id=call.message.chat.id,
            text="⚠️ Сталася помилка при створенні рахунку. Спробуйте ще раз пізніше.",
        )
