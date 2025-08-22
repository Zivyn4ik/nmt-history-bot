# bot/handlers_wipe.py
from __future__ import annotations

from aiogram import Router, Bot
from aiogram.filters import Command
from aiogram.types import Message

from .db import Session, Subscription, User
from .config import settings

router = Router()

@router.message(Command("wipe_me"))
async def wipe_me(message: Message, bot: Bot):
    user_id = message.from_user.id
    kicked = False
    try:
        await bot.ban_chat_member(settings.CHANNEL_ID, user_id)
        await bot.unban_chat_member(settings.CHANNEL_ID, user_id)
        kicked = True
    except Exception:
        pass

    affected = 0
    try:
        async with Session() as s:
            sub = await s.get(Subscription, user_id)
            if sub:
                await s.delete(sub)
                affected += 1
            usr = await s.get(User, user_id)
            if usr:
                await s.delete(usr)
                affected += 1
            await s.commit()
    except Exception:
        affected = -1

    parts = []
    if kicked:
        parts.append("Вас видалено з каналу.")
    if affected >= 0:
        parts.append(f"Дані у БД очищено (об'єкти: {affected}).")
    else:
        parts.append("Частину даних очистити не вдалося.")
    parts.append("Для повторного доступу оформіть підписку ще раз: /buy")
    await message.answer("🧹 " + " ".join(parts))
