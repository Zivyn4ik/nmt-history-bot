from __future__ import annotations
import logging
from datetime import datetime, timedelta, timezone
from aiogram import Router, Bot
from aiogram.types import Message, CallbackQuery
from aiogram.filters import Text
from bot.services import ensure_user, get_subscription_status, has_active_access
from bot.payments.wayforpay import create_invoice
from bot.db import Session, Payment
from bot.config import settings

router = Router()
log = logging.getLogger(__name__)
UTC = timezone.utc

async def send_invoice_link(bot: Bot, chat_id: int, user_id: int, amount: float, description: str):
    url, order_ref = await create_invoice(user_id=user_id, amount=amount, product_name=description)
    async with Session() as s:
        s.add(Payment(
            user_id=user_id,
            order_ref=order_ref,
            amount=amount,
            currency="UAH",
            status="pending",
            created_at=datetime.now(UTC)
        ))
        await s.commit()
    await bot.send_message(chat_id, f"💳 Для оплаты перейдите по ссылке:\n{url}")

@router.message(Text(text="Оформить подписку"))
async def buy_subscription(message: Message):
    await ensure_user(message.from_user)
    user_id = message.from_user.id
    sub_info = await get_subscription_status(user_id)
    if await has_active_access(user_id):
        await message.answer("✅ У вас уже есть активная подписка.")
        return
    await send_invoice_link(message.bot, message.chat.id, user_id, amount=200, description="Подписка на 1 месяц")

@router.message(Text(text="Проверить подписку"))
async def check_subscription(message: Message):
    await ensure_user(message.from_user)
    user_id = message.from_user.id
    sub_info = await get_subscription_status(user_id)
    if await has_active_access(user_id):
        paid_until = sub_info.paid_until.strftime("%d.%m.%Y %H:%M") if sub_info.paid_until else "неизвестно"
        await message.answer(f"✅ Ваша подписка активна до {paid_until}")
    else:
        await message.answer("❌ У вас нет активной подписки.")
