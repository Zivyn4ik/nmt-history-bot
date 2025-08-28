from __future__ import annotations
import logging
from aiogram import Router, Bot
from aiogram.types import Message
from aiogram.filters import Command
from bot.db import Session, User, Subscription, Payment, PaymentToken
from bot.services import ensure_user

router = Router()
log = logging.getLogger(__name__)

@router.message(Command(commands=["wipe"]))
async def cmd_wipe(message: Message):
    """Полная очистка данных пользователя (только для тестов / отладки)."""
    await ensure_user(message.from_user)
    user_id = message.from_user.id
    async with Session() as s:
        await s.execute(Payment.__table__.delete().where(Payment.user_id == user_id))
        await s.execute(PaymentToken.__table__.delete().where(PaymentToken.user_id == user_id))
        await s.execute(Subscription.__table__.delete().where(Subscription.user_id == user_id))
        await s.execute(User.__table__.delete().where(User.id == user_id))
        await s.commit()
    await message.answer("🧹 Ваши данные успешно удалены.")
