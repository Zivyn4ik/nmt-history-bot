from __future__ import annotations

import logging
from aiogram import Router, Bot
from aiogram.filters import Command
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton

from bot.config import settings
from bot.payments.wayforpay import create_invoice

router = Router()
log = logging.getLogger("handlers.buy")

@router.message(Command("buy"))
async def cmd_buy(message: Message, bot: Bot):
    """
    Создаёт инвойс WayForPay и отправляет кнопку "Оплатити".
    При любой ошибке покажет понятное сообщение и запишет трассу в логи.
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
        inline_keyboard=[[InlineKeyboardButton(text="Оплатити", url=url)]]
    )
    await message.answer("Рахунок на 1 місяць сформовано. Натисніть «Оплатити».", reply_markup=kb)