# bot/handlers_buy.py
from __future__ import annotations

import logging
from aiogram import Router, Bot
from aiogram.filters import Command
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton

from .payments.wayforpay import create_invoice
from .config import settings

router = Router()
log = logging.getLogger("handlers.buy")


async def send_buy_button(message: Message, bot: Bot):
    """
    Отправить красивую кнопку оплаты (inline URL), без текста /buy.
    """
    user_id = message.from_user.id
    try:
        url = await create_invoice(
            user_id=user_id,
            amount=settings.PRICE,
            currency=settings.CURRENCY,
            product_name=getattr(settings, "PRODUCT_NAME", "Channel subscription (1 month)"),
        )
    except Exception as e:
        log.exception("Failed to create invoice for user %s: %s", user_id, e)
        await message.answer(f"Не вдалося сформувати рахунок. Причина: {e}")
        return

    kb = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="💳 Оплатити підписку", url=url)]]
    )
    await message.answer("Натисніть кнопку нижче, щоб оформити підписку:", reply_markup=kb)


@router.message(Command("buy"))
async def cmd_buy(message: Message, bot: Bot):
    # /buy теперь просто вызывает ту же кнопку
    await send_buy_button(message, bot)
