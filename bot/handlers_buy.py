from __future__ import annotations

import logging
import uuid
from aiogram import Router, Bot
from aiogram.filters import Command
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton

from bot.config import settings
from bot.db import Session, PaymentToken, Payment
from bot.payments.wayforpay import create_invoice
from bot.services import activate_or_extend

router = Router()
log = logging.getLogger("handlers.buy")


@router.message(Command("buy"))
async def cmd_buy(message: Message, bot: Bot):
    user_id = message.from_user.id

    token = uuid.uuid4().hex
    try:
        async with Session() as session:
            # Проверяем, есть ли уже pending токен
            existing = await session.execute(
                PaymentToken.__table__.select().where(
                    (PaymentToken.user_id == user_id) & (PaymentToken.status == "pending")
                )
            )
            old_token = existing.scalar_one_or_none()
            if old_token:
                token = old_token.token  # переиспользуем существующий
            else:
                session.add(PaymentToken(user_id=user_id, token=token, status="pending"))
                await session.commit()
        log.info("🔑 Payment token ready for user %s: %s", user_id, token)
    except Exception as e:
        log.exception("Failed to create payment token for user %s: %s", user_id, e)
        await message.answer("Не вдалося підготувати оплату. Спробуйте ще раз.")
        return

    try:
        url, order_ref = await create_invoice(
            user_id=user_id,
            amount=settings.PRICE,
            currency=settings.CURRENCY,
            product_name=getattr(settings, "PRODUCT_NAME", "Channel subscription (1 month)"),
            start_token=token,
        )
        async with Session() as session:
            # Проверяем, есть ли уже запись Payment с этим order_ref
            existing_payment = await session.execute(
                Payment.__table__.select().where(Payment.order_ref == order_ref)
            )
            if not existing_payment.scalar_one_or_none():
                session.add(Payment(
                    user_id=user_id,
                    order_ref=order_ref,
                    amount=settings.PRICE,
                    currency=settings.CURRENCY,
                    status="created",
                ))
                await session.commit()
    except Exception as e:
        log.exception("Failed to create invoice for user %s: %s", user_id, e)
        await message.answer("Не вдалося сформувати рахунок. Спробуйте ще раз пізніше.")
        return

    kb = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="Оплатити", url=url)]]
    )
    await message.answer(
        "✅ Рахунок на 1 місяць сформовано!\n"
        "Натисніть «Оплатити», а після успішної оплати ви автоматично отримаєте доступ.",
        reply_markup=kb
    )
