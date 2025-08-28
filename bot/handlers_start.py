from aiogram import Router, F
from aiogram.types import Message, KeyboardButton, ReplyKeyboardMarkup
from aiogram.filters import CommandStart
from bot.db import async_session_maker, get_or_create_user

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
    async with async_session_maker() as session:
        await get_or_create_user(session, message.from_user.id)
    await message.answer(WELCOME, reply_markup=main_kb())
