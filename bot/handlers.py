from __future__ import annotations

from aiogram import Router, F, Bot
from aiogram.filters import CommandStart
from aiogram.types import (
    Message,
    ChatJoinRequest,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)
from aiogram.exceptions import TelegramBadRequest

from .config import settings
from .services import ensure_user, get_subscription_status, has_active_access

router = Router()


def _buy_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="💳 Оформити підписку", callback_data="buy")]]
    )


async def _is_member(bot: Bot, user_id: int) -> bool:
    """Факт членства в канале как резервный сигнал 'доступ есть'."""
    try:
        cm = await bot.get_chat_member(settings.CHANNEL_ID, user_id)
        return cm.status in ("member", "administrator", "creator")
    except TelegramBadRequest:
        return False
    except Exception:
        return False


@router.message(CommandStart())
async def cmd_start(message: Message, bot: Bot):
    """Показываем статус подписки. Если БД не знает про оплату, но юзер уже в канале —
    считаем доступ активным и не мучаем его."""
    await ensure_user(message.from_user)
    uid = message.from_user.id

    sub = await get_subscription_status(uid)
    has_db_access = bool(getattr(sub, "status", None) == "active" and getattr(sub, "paid_until", None))

    if has_db_access:
        invite = getattr(settings, "TG_JOIN_REQUEST_URL", "")
        text = f"✅ Підписка активна до <b>{sub.paid_until.date()}</b>."
        if invite:
            text += f"\nЯкщо ви ще не в каналі — перейдіть за посиланням:\n{invite}"
        await message.answer(text)
        return

    # Fallback: нет записи в БД, но пользователь уже член канала
    if await _is_member(bot, uid):
        await message.answer(
            "✅ Ви вже маєте доступ до каналу (ви є його учасником).\n"
            "Якщо оплата була щойно — статус у системі оновиться автоматично."
        )
        return

    # Нет доступа вообще
    await message.answer(
        "❌ Підписки немає або вона завершилась.\n\n"
        "Щоб отримати доступ — натисніть кнопку нижче 👇",
        reply_markup=_buy_kb(),
    )


@router.chat_join_request(F.chat.id == settings.CHANNEL_ID)
async def on_join_request(event: ChatJoinRequest, bot: Bot):
    """Если у пользователя есть оплаченный доступ — одобряем заявку.
    Иначе — отправляем кнопку покупки."""
    uid = event.from_user.id
    if await has_active_access(uid):
        try:
            await bot.approve_chat_join_request(chat_id=settings.CHANNEL_ID, user_id=uid)
        except Exception:
            pass
    else:
        try:
            await bot.send_message(
                uid,
                "Щоб увійти до каналу — оформіть підписку:",
                reply_markup=_buy_kb(),
            )
        except Exception:
            pass
