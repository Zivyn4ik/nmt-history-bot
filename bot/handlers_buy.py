# bot/handlers_buy.py
from __future__ import annotations

import logging
from aiogram import Router, Bot
from aiogram.filters import Command
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton

# ВАЖНО: используем ваш рабочий модуль WayForPay из архива!
from .payments.wayforpay import create_invoice
from .config import settings

router = Router()
log = logging.getLogger("handlers.buy")


async def send_buy_button(message: Message, bot: Bot):
    """
    Красиво показываем кнопку оплаты вместо текста /buy.
    НИКАКИХ изменений в вашей логике подписей/запросов к WFP.
    """
    user_id = message.from_user.id
    try:
        url = await create_invoice(
            user_id=user_id,
            amount=settings.PRICE,
            currency=settings.CURRENCY,
            product_name=getattr(settings, "PRODUCT_NAME", "Channel subscription"),
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
    # Команда /buy теперь просто выводит красивую кнопку
    await send_buy_button(message, bot)
