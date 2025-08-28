from datetime import timedelta, datetime, timezone

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from db import async_session_maker, User
from services import remaining_days

router = Router(name="admin")

# Укажи свой Telegram ID здесь, чтобы ограничить доступ к админ-командам
ADMIN_IDS = set(7534323874)  # пример: {123456789}

def admin_only(func):
    async def wrapper(message: Message, *args, **kwargs):
        if message.from_user.id not in ADMIN_IDS:
            return
        return await func(message, *args, **kwargs)
    return wrapper

@router.message(Command("users"))
@admin_only
async def cmd_users(message: Message):
    async with async_session_maker() as session:
        result = await session.execute(User.__table__.select().limit(1000))
        rows = result.fetchall()
    lines = []
    for r in rows:
        u = User(**dict(r))
        days = remaining_days(u)
        lines.append(f"{u.id}: {u.status} до {u.end_date} (ост {days})")
    text = "\n".join(lines) if lines else "Пусто"
    await message.answer(text)

@router.message(Command("extend"))
@admin_only
async def cmd_extend(message: Message):
    parts = message.text.split()
    if len(parts) != 3:
        await message.answer("Формат: /extend <user_id> <days>")
        return
    uid = int(parts[1]); days = int(parts[2])
    async with async_session_maker() as session:
        u = await session.get(User, uid)
        if not u:
            await message.answer("Нет такого пользователя")
            return
        now = datetime.utcnow()
        if u.end_date and u.end_date > now:
            u.end_date = u.end_date + timedelta(days=days)
        else:
            u.end_date = now + timedelta(days=days)
            u.status = "ACTIVE"
        await session.commit()
    await message.answer("Готово")

@router.message(Command("ban"))
@admin_only
async def cmd_ban(message: Message):
    parts = message.text.split()
    if len(parts) != 2:
        await message.answer("Формат: /ban <user_id>")
        return
    uid = int(parts[1])
    async with async_session_maker() as session:
        u = await session.get(User, uid)
        if not u:
            await message.answer("Нет такого пользователя")
            return
        u.status = "INACTIVE"
        await session.commit()
    await message.answer("Отключено")
