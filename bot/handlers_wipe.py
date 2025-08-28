from __future__ import annotations

from aiogram import Router, Bot
from aiogram.filters import Command
from aiogram.types import Message
from aiogram.enums import ChatType
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy import text
import logging

from bot.config import settings

log = logging.getLogger(__name__)
router = Router()

_engine = create_async_engine(settings.DATABASE_URL, future=True, echo=False)

async def _wipe_user_data(user_id: int) -> int:
    """
    Полностью очищает данные пользователя, кроме subscriptions.
    В subscriptions выставляет статус 'expired'.
    """
    affected = 0
    async with _engine.begin() as conn:
        res = await conn.execute(text("SELECT name FROM sqlite_master WHERE type='table'"))
        tables = [r[0] for r in res if r[0] not in ("sqlite_sequence", "alembic_version")]

        for t in tables:
            if t == "subscriptions":
                continue
            cols = await conn.execute(text(f'PRAGMA table_info("{t}")'))
            colnames = [row[1] for row in cols]
            if "user_id" in colnames:
                await conn.execute(text(f'DELETE FROM "{t}" WHERE user_id = :uid'), {"uid": user_id})
                affected += 1
            elif t in ("users", "user") and "id" in colnames:
                await conn.execute(text(f'DELETE FROM "{t}" WHERE id = :uid'), {"uid": user_id})
                affected += 1

        await conn.execute(text("""
            INSERT INTO subscriptions (user_id, status, paid_until, grace_until, last_reminded_on, updated_at)
            VALUES (:uid, 'expired', NULL, NULL, NULL, CURRENT_TIMESTAMP)
            ON CONFLICT(user_id) DO UPDATE SET
              status='expired',
              paid_until=NULL,
              grace_until=NULL,
              last_reminded_on=NULL,
              updated_at=CURRENT_TIMESTAMP
        """), {"uid": user_id})
        affected += 1

    return affected


@router.message(Command(commands=["unsubscribe", "wipe_me"]))
async def cmd_unsubscribe(message: Message, bot: Bot):
    """
    Полностью удаляет пользователя из канала и базы данных.
    Работает только в личных сообщениях.
    """
    if message.chat.type != ChatType.PRIVATE:
        return

    user_id = message.from_user.id

    # 1) отклоняем активную заявку
    try:
        await bot.decline_chat_join_request(chat_id=settings.CHANNEL_ID, user_id=user_id)
    except Exception:
        pass

    # 2) кик из канала
    kicked = False
    try:
        await bot.ban_chat_member(chat_id=settings.CHANNEL_ID, user_id=user_id)
        await bot.unban_chat_member(chat_id=settings.CHANNEL_ID, user_id=user_id)
        kicked = True
    except Exception:
        pass

    # 3) очистка БД
    try:
        affected = await _wipe_user_data(user_id)
    except Exception as e:
        affected = -1
        log.exception("wipe_me DB error:", e)

    parts = []
    if kicked:
        parts.append("Вас видалено з каналу.")
    if affected >= 0:
        parts.append(f"Дані у БД очищено (таблиць: {affected}).")
    else:
        parts.append("Частину даних очистити не вдалося.")
    parts.append("Для повторного доступу оформіть підписку ще раз: /buy")

    await message.answer("🧹 " + " ".join(parts))
