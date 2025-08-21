# bot/handlers_wipe.py
from __future__ import annotations

from aiogram import Router, F, Bot
from aiogram.filters import Command
from aiogram.types import Message, ChatJoinRequest
from aiogram.enums import ChatType

from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy import text

from .config import settings

router = Router()

# отдельный «внутренний» движок для прямых SQL-команд (не мешаем вашему ORM)
_engine = create_async_engine(settings.DATABASE_URL, future=True, echo=False)


async def _wipe_user_data(user_id: int) -> int:
    """
    Удаляет все записи по пользователю из всех таблиц,
    где есть столбец user_id. Возвращает кол-во затронутых таблиц.
    """
    affected = 0
    async with _engine.begin() as conn:
        # список таблиц
        res = await conn.execute(text("SELECT name FROM sqlite_master WHERE type='table'"))
        tables = [r[0] for r in res if r[0] not in ("sqlite_sequence", "alembic_version")]

        for t in tables:
            # список колонок
            cols = await conn.execute(text(f'PRAGMA table_info("{t}")'))
            colnames = [row[1] for row in cols]  # [cid, name, type, notnull, dflt_value, pk]

            if "user_id" in colnames:
                await conn.execute(text(f'DELETE FROM "{t}" WHERE user_id = :uid'), {"uid": user_id})
                affected += 1
            elif t in ("users", "user") and "id" in colnames:
                # на случай отдельной таблицы users
                await conn.execute(text(f'DELETE FROM "{t}" WHERE id = :uid'), {"uid": user_id})
                affected += 1
    return affected


@router.message(Command(commands=["unsubscribe", "wipe_me"]))
async def cmd_unsubscribe(message: Message, bot: Bot):
    """
    ТЕСТОВАЯ команда: удаляет все данные пользователя и выкидывает из канала.
    Работает только в личке с ботом.
    """
    if message.chat.type != ChatType.PRIVATE:
        return  # не реагируем в группах/каналах

    user_id = message.from_user.id

    # 1) закрыть/отклонить возможную заявку на вступление
    try:
        await bot.decline_chat_join_request(chat_id=settings.CHANNEL_ID, user_id=user_id)
    except Exception:
        pass  # если заявки нет — ок

    # 2) кикнуть из канала (баним и сразу разбаниваем, чтобы можно было зайти снова)
    kicked = False
    try:
        await bot.ban_chat_member(chat_id=settings.CHANNEL_ID, user_id=user_id)
        await bot.unban_chat_member(chat_id=settings.CHANNEL_ID, user_id=user_id)
        kicked = True
    except Exception:
        # не подписан / нет прав — игнорируем
        pass

    # 3) удалить данные в БД
    try:
        affected = await _wipe_user_data(user_id)
    except Exception as e:
        affected = -1
        # отправим минимальную диагностику
        await message.answer("⚠️ Не удалось очистить записи в БД. Детали отправлены в лог.")
        print("wipe_me DB error:", e)

    # 4) ответ пользователю
    parts = []
    if kicked:
        parts.append("Вас удалили из канала.")
    if affected >= 0:
        parts.append(f"Дані у БД очищено (таблиць: {affected}).")
    else:
        parts.append("Частину даних очистити не вдалося.")

    parts.append("Для повторного доступу оформіть підписку ще раз: /buy")
    await message.answer("🧹 " + " ".join(parts))


# (опционально) если хотите ещё и автоподтверждение заявок оставить здесь —
# можно также ловить ChatJoinRequest; базовая версия уже есть в ваших хендлерах.
