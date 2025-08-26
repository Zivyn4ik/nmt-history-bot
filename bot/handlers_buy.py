from __future__ import annotations

import logging
import uuid
from aiogram import Router, Bot
from aiogram.filters import Command
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton

from bot.config import settings
from bot.db import Session, PaymentToken
from bot.payments.wayforpay import create_invoice

router = Router()
log = logging.getLogger("handlers.buy")


@router.message(Command("buy"))
async def cmd_buy(message: Message, bot: Bot):
    """
    Создаёт инвойс WayForPay и отправляет кнопку "Оплатити".
    Также генерирует одноразовый токен для проверки оплаты после callback.
    """
    user_id = message.from_user.id

    # 1️⃣ Создаём токен для пользователя
    token = uuid.uuid4().hex
    try:
        async with Session() as session:
            session.add(PaymentToken(user_id=user_id, token=token, status="pending"))
            await session.commit()
        log.info("🔑 Payment token created for user %s: %s", user_id, token)
    except Exception as e:
        log.exception("Failed to create payment token for user %s: %s", user_id, e)
        await message.answer(f"Не вдалося підготувати оплату. Спробуйте ще раз.")
        return

    # 2️⃣ Создаём инвойс WayForPay
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

    # 3️⃣ Отправляем кнопку с оплатой
    kb = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="Оплатити", url=url)]]
    )
    await message.answer(
        "Рахунок на 1 місяць сформовано. Натисніть «Оплатити».",
        reply_markup=kb
    )
