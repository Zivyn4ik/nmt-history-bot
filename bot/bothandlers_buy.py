# bot/handlers_buy.py
from __future__ import annotations

import logging
from aiogram import Router, Bot
from aiogram.filters import Command
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton

from .config import settings
from .payments.wayforpay import create_invoice

router = Router()
log = logging.getLogger("handlers.buy")


@router.message(Command("buy"))
async def cmd_buy(message: Message, bot: Bot):
    """
    Надійний хендлер оплати:
    - створює інвойс WayForPay;
    - відправляє кнопку "Оплатити";
    - у разі помилки показує її користувачу і пише в логи.
    """
    user_id = message.from_user.id
    try:
        url = await create_invoice(
            user_id=user_id,
            amount=settings.PRICE,
            currency=settings.CURRENCY,
            product_name=getattr(settings, "PRODUCT_NAME", "Channel subscription (1 month)"),
        )
        kb = InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="Оплатити", url=url)]]
        )
        await message.answer("Рахунок на 1 місяць сформовано. Натисніть «Оплатити».", reply_markup=kb)

    except Exception as e:
        # Лог із трасою — щоб у Render було видно деталі
        log.exception("Failed to create invoice for user %s: %s", user_id, e)

        # Коротке пояснення користувачу
        msg = f"Не вдалося сформувати рахунок. Причина: {e}"
        s = str(e).lower()
        if "signature" in s or "1113" in s:
            msg += (
                "\n\nПідказка: у WayForPay підпис має бути HMAC-MD5, а суми — у форматі '100.00' "
                "(ті самі рядки і в JSON, і в підписі). Оновіть файл bot/payments/wayforpay.py згідно з нашим варіантом."
            )
        await message.answer(msg)
