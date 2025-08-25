from __future__ import annotations

from aiogram import Router, Bot
from aiogram.filters import Command
from aiogram.types import Message
from aiogram.enums import ChatType

from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy import text

from bot.config import settings

import logging

log = logging.getLogger(__name__)
router = Router()

# отдельный лёгкий движок для прямых SQL-команд
_engine = create_async_engine(settings.DATABASE_URL, future=True, echo=False)


async def _wipe_user_data(user_id: int) -> int:
    """
    Удаляет ВСЕ данные пользователя, кроме таблицы subscriptions.
    В subscriptions мы не удаляем строку, а выставляем 'expired' и updated_at=NOW,
    чтобы можно было распознать и игнорировать "старые" коллбеки WayForPay.
    Возвращает количество затронутых таблиц.
    """
    affected = 0
    async with _engine.begin() as conn:
        # перечень таблиц
        res = await conn.execute(text("SELECT name FROM sqlite_master WHERE type='table'"))
        tables = [r[0] for r in res if r[0] not in ("sqlite_sequence", "alembic_version")]

        for t in tables:
            if t == "subscriptions":
                # пропускаем здесь — обработаем UPSERT ниже
                continue

            # удаляем строки по user_id (если колонка есть)
            cols = await conn.execute(text(f'PRAGMA table_info("{t}")'))
            colnames = [row[1] for row in cols]
            if "user_id" in colnames:
                await conn.execute(text(f'DELETE FROM "{t}" WHERE user_id = :uid'), {"uid": user_id})
                affected += 1
            elif t in ("users", "user") and "id" in colnames:
                await conn.execute(text(f'DELETE FROM "{t}" WHERE id = :uid'), {"uid": user_id})
                affected += 1

        # subscriptions: ставим "мечту" (expired) с обновлённым updated_at
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
    ТЕСТОВАЯ команда: выкидывает пользователя из канала и полностью очищает его данные.
    Работает только в личке.
    """
    if message.chat.type != ChatType.PRIVATE:
        return

    user_id = message.from_user.id

    # 1) отклонить активную заявку (если есть)
    try:
        await bot.decline_chat_join_request(chat_id=settings.CHANNEL_ID, user_id=user_id)
    except Exception:
        pass

    # 2) кикнуть из канала (бан/анбан — чтобы можно было зайти снова)
    kicked = False
    try:
        await bot.ban_chat_member(chat_id=settings.CHANNEL_ID, user_id=user_id)
        await bot.unban_chat_member(chat_id=settings.CHANNEL_ID, user_id=user_id)
        kicked = True
    except Exception:
        pass

    # 3) очистка БД (с tombstone в subscriptions)
    try:
        affected = await _wipe_user_data(user_id)
    except Exception as e:
        affected = -1
        log.exception("wipe_me DB error:", e)

    # 4) ответ
    parts = []
    if kicked:
        parts.append("Вас видалено з каналу.")
    if affected >= 0:
        parts.append(f"Дані у БД очищено (таблиць: {affected}).")
    else:
        parts.append("Частину даних очистити не вдалося.")
    parts.append("Для повторного доступу оформіть підписку ще раз: /buy")
    await message.answer("🧹 " + " ".join(parts))
