from __future__ import annotations

import logging
import uuid
import asyncio
from aiogram import Router, Bot
from aiogram.filters import Command
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton

from bot.config import settings
from bot.db import Session, PaymentToken, Payment
from bot.payments.wayforpay import create_invoice
from bot.services import activate_or_extend

router = Router()
log = logging.getLogger("handlers.buy")


async def wait_for_payment(bot: Bot, user_id: int, token: str, timeout: int = 35):
    """
    Проверяет статус оплаты каждую секунду в течение timeout секунд.
    Как только оплата подтверждена — активирует подписку и отправляет ссылку на канал.
    """
    async with Session() as s:
        start_time = asyncio.get_event_loop().time()
        message: Message = await bot.send_message(user_id, "⏳ Генерируем приглашение на канал…")
        while (asyncio.get_event_loop().time() - start_time) < timeout:
            res = await s.execute(
                select(PaymentToken).where(PaymentToken.token == token)
            )
            token_obj = res.scalar_one_or_none()
            if token_obj and token_obj.status == "paid":
                token_obj.status = "used"
                await s.commit()
                await message.delete()
                await activate_or_extend(bot, user_id)
                return True
            await asyncio.sleep(1)
        await message.edit_text("❌ Оплата не подтвердилась за 35 секунд. Попробуйте позже.")
        return False


@router.message(Command("buy"))
async def cmd_buy(message: Message, bot: Bot):
    """
    Создает одноразовый токен, Payment объект и инвойс WayForPay.
    После успешной оплаты запускает wait_for_payment для выдачи ссылки на канал.
    """
    user_id = message.from_user.id
    token = uuid.uuid4().hex

    # 1️⃣ Создаем токен
    try:
        async with Session() as session:
            session.add(PaymentToken(user_id=user_id, token=token, status="pending"))
            await session.commit()
        log.info("🔑 Payment token создан для пользователя %s: %s", user_id, token)
    except Exception as e:
        log.exception("Failed to create payment token for user %s: %s", user_id, e)
        await message.answer("Не удалось подготовить оплату. Попробуйте еще раз.")
        return

    # 2️⃣ Создаем инвойс WayForPay и Payment
    try:
        url, order_ref = await create_invoice(
            user_id=user_id,
            amount=settings.PRICE,
            currency=settings.CURRENCY,
            product_name=getattr(settings, "PRODUCT_NAME", "Channel subscription (1 month)"),
            start_token=token,  # для редиректа на /wfp/return?token=...
        )

        async with Session() as session:
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
        await message.answer("Не удалось сформировать счет. Попробуйте позже.")
        return

    # 3️⃣ Отправляем кнопку "Оплатить"
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Оплатить", url=url)]])
    await message.answer(
        "✅ Рахунок на 1 місяць сформовано!\n"
        "Натисніть «Оплатити», а после успешной оплаты бот автоматически отправит вам ссылку на канал.",
        reply_markup=kb
    )

    # 4️⃣ Ждем подтверждения оплаты и выдаем приглашение
    await wait_for_payment(bot, user_id, token)
