from __future__ import annotations

from aiogram import Router, F, Bot
from aiogram.filters import CommandStart
from aiogram.types import (
    Message,
    CallbackQuery,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)
from aiogram.exceptions import TelegramBadRequest

from .config import settings
from .services import ensure_user, get_subscription_status

router = Router()


def _main_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💳 Оформити підписку", callback_data="buy")],
        [InlineKeyboardButton(text="✅ Перевірка статусу підписки", callback_data="check")],
        [InlineKeyboardButton(text="📞 Підтримка @zivyn4ik", url="https://t.me/zivyn4ik")],
    ])


def _buy_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💳 Оформити підписку", callback_data="buy")]
    ])


async def _is_member(bot: Bot, user_id: int) -> bool:
    try:
        cm = await bot.get_chat_member(settings.CHANNEL_ID, user_id)
        return cm.status in ("member", "administrator", "creator")
    except TelegramBadRequest:
        return False
    except Exception:
        return False


@router.message(CommandStart())
async def start_handler(message: Message):
    text = (
        "👋 <b>Вітаємо у навчальному боті HMT 2026 | Історія України!</b>\n\n"
        "📚 Тут ви отримаєте доступ до:\n"
        "• Таблиць для підготовки до НМТ\n"
        "• Тестів та завдань з поясненнями\n"
        "• Корисних матеріалів від викладачів\n\n"
        "🧭 Скористайтесь кнопками нижче."
    )
    await message.answer(text, reply_markup=_main_menu_kb())


@router.callback_query(F.data == "buy")
async def cb_buy(call: CallbackQuery, bot: Bot):
    from .handlers_buy import cmd_buy
    await call.answer()
    await cmd_buy(call.message, bot)


@router.callback_query(F.data == "check")
async def cb_check(call: CallbackQuery, bot: Bot):
    await call.answer()
    user = call.from_user
    await ensure_user(user)

    sub = await get_subscription_status(user.id)
    has_db_access = bool(getattr(sub, "status", None) == "active" and getattr(sub, "paid_until", None))

    if has_db_access:
        invite = getattr(settings, "TG_JOIN_REQUEST_URL", "")
        text = f"✅ Підписка активна до <b>{sub.paid_until.date()}</b>."
        if invite:
            text += f"\nЯкщо ви ще не в каналі — перейдіть за посиланням:\n{invite}"
        await call.message.answer(text)
        return

    if await _is_member(bot, user.id):
        await call.message.answer(
            "✅ Ви вже маєте доступ до каналу (ви є його учасником).\n"
            "Якщо оплата була щойно — статус у системі оновиться автоматично."
        )
        return

    await call.message.answer(
        "❌ Підписки немає або вона завершилась.\n\n"
        "Щоб отримати доступ — натисніть кнопку нижче 👇",
        reply_markup=_buy_kb(),
    )
