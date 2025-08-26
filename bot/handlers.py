from __future__ import annotations

import logging
import uuid
from aiogram import Router, Bot
from aiogram.filters import Command
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton
from sqlalchemy import delete

from bot.config import settings
from bot.db import Session, PaymentToken
from bot.payments.wayforpay import create_invoice

router = Router()
log = logging.getLogger("handlers.buy")


@router.message(Command("buy"))
async def cmd_buy(message: Message, bot: Bot):
    """
    Создаёт одноразовый токен, инвойс WayForPay и отправляет кнопку "Оплатити".
    После оплаты WFP вернёт пользователя на /wfp/return?token=<...> → t.me/<bot>?start=<token>
    """
    user_id = message.from_user.id

    # 1) Чистим старые pending-токены пользователя (чтобы callback точно метил один)
    try:
        async with Session() as session:
            await session.execute(
                delete(PaymentToken).where(
                    PaymentToken.user_id == user_id,
                    PaymentToken.status == "pending"
                )
            )
            await session.commit()
    except Exception:
        pass

    # 2) Создаём новый pending-токен
    token = uuid.uuid4().hex
    try:
        async with Session() as session:
            session.add(PaymentToken(user_id=user_id, token=token, status="pending"))
            await session.commit()
        log.info("🔑 Payment token created for user %s: %s", user_id, token)
    except Exception as e:
        log.exception("Failed to create payment token for user %s: %s", user_id, e)
        await message.answer("Не вдалося підготувати оплату. Спробуйте ще раз.")
        return

    # 3) Создаём инвойс WayForPay с returnUrl, содержащим token
    try:
        url = await create_invoice(
            user_id=user_id,
            amount=settings.PRICE,
            currency=settings.CURRENCY,
            product_name=getattr(settings, "PRODUCT_NAME", "Channel subscription (1 month)"),
            start_token=token,  # ⬅️ ПЕРЕДАЁМ ТОКЕН
        )
    except Exception as e:
        log.exception("Failed to create invoice for user %s: %s", user_id, e)
        await message.answer(f"Не вдалося сформувати рахунок. Причина: {e}")
        return

    # 4) Кнопка "Оплатити"
    kb = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="Оплатити", url=url)]]
    )
    await message.answer(
        "Рахунок на 1 місяць сформовано. Натисніть «Оплатити».",
        reply_markup=kb
    )
