# bot/handlers_start.py
from __future__ import annotations

from aiogram import Router, Bot, F
from aiogram.filters import CommandStart
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton

from .services import ensure_user, has_active_access, get_subscription_status
from .config import settings

router = Router()


def _main_keyboard(has_access: bool) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text="💳 Оформити підписку", callback_data="buy")],
        [InlineKeyboardButton(text="✅ Перевірка статусу підписки", callback_data="check")],
        [InlineKeyboardButton(text="🛟 Підтримка @zivyn4ik", url="https://t.me/zivyn4ik")],
    ]
    if has_access:
        rows[0] = [InlineKeyboardButton(text="➡️ Увійти в канал", url=settings.TG_JOIN_REQUEST_URL)]
    return InlineKeyboardMarkup(inline_keyboard=rows)


@router.message(CommandStart())
async def cmd_start(message: Message, bot: Bot):
    await ensure_user(message.from_user)

    active = await has_active_access(message.from_user.id)
    kb = _main_keyboard(active)

    # ВАЖНО: никаких «Підписка активна…» если нет confirmed access
    if active:
        st = await get_subscription_status(message.from_user.id)
        until_text = st.paid_until.strftime("%Y-%m-%d") if st.paid_until else "невідомо"
        await message.answer(
            f"👋 Вітаємо у навчальному боті НМТ 2026 | Історія України!\n\n"
            f"✅ Підписка активна до {until_text}.",
            reply_markup=kb,
        )
    else:
        await message.answer(
            "👋 Вітаємо у навчальному боті НМТ 2026 | Історія України!\n\n"
            "❌ Підписки немає або вона завершилась. Скористайтесь кнопками нижче.",
            reply_markup=kb,
        )


# -------- callbacks --------

@router.callback_query(F.data == "buy")
async def cb_buy(call: CallbackQuery, bot: Bot):
    from .handlers_buy import send_buy_button
    await call.answer()
    await send_buy_button(call.message, bot)


@router.callback_query(F.data == "check")
async def cb_check(call: CallbackQuery, bot: Bot):
    await call.answer()
    # Просто повторно показываем старт — там уже корректная логика статуса
    await cmd_start(call.message, bot)
