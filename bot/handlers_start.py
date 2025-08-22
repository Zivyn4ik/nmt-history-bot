# bot/handlers_start.py
from __future__ import annotations

from aiogram import Router, F, Bot
from aiogram.filters import CommandStart
from aiogram.types import (
    Message,
    CallbackQuery,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)

router = Router()


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
        "💳 Щоб отримати доступ — скористайтесь кнопками нижче."
    )
    await message.answer(text, reply_markup=_start_keyboard())


# --- callbacks ---

@router.callback_query(F.data == "buy")
async def cb_buy(call: CallbackQuery, bot: Bot):
    # вызывем уже готовый обработчик покупки
    from .handlers_buy import cmd_buy  # локальный импорт, чтобы избежать цикличности
    await call.answer()
    # эмулируем /buy для текущего чата
    await cmd_buy(call.message, bot)


@router.callback_query(F.data == "check")
async def cb_check(call: CallbackQuery, bot: Bot):
    # повторно показываем старт, т.к. в /start у тебя уже выводится статус
    from .handlers import cmd_start  # локальный импорт
    await call.answer()
    await cmd_start(call.message, bot)


