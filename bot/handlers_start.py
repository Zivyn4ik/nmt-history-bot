from __future__ import annotations

from aiogram import Router, F, Bot
from aiogram.types import Message, KeyboardButton, ReplyKeyboardMarkup
from aiogram.filters import CommandStart
from sqlalchemy import select
from datetime import datetime, timezone


from bot.config import settings
from bot.services import ensure_user, get_subscription_status, activate_or_extend
from bot.db import Session, PaymentToken, async_session_maker, get_or_create_user

import asyncio

router = Router(name="start")

WELCOME = (
    "👋 <b>Вітаємо у навчальному боті HMT 2026 | Історія України!</b>\n\n"
    "📚 Тут ви отримаєте доступ до:\n"
    "• Таблиць для підготовки до НМТ\n"
    "• Тестів та завдань з поясненнями\n"
    "• Корисних матеріалів від викладачів\n\n"
    "🧭 Скористайтесь кнопками нижче."
)

def main_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="Оформить подписку")],
            [KeyboardButton(text="Проверить подписку")],
            [KeyboardButton(text="Помощь")],
        ],
        resize_keyboard=True
    )

@router.message(CommandStart())
async def cmd_start(message: Message):
    # deep-link ?start=paid → сразу запускаем проверку в handlers_buy
    async with async_session_maker() as session:
        await get_or_create_user(session, message.from_user.id)
    await message.answer(WELCOME, reply_markup=main_kb())

