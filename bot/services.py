from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional
import logging

from aiogram import Bot
from aiogram.enums.chat_member_status import ChatMemberStatus
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update

from bot.config import settings
from bot.db import Session, User, Subscription

log = logging.getLogger(__name__)
UTC = timezone.utc
now = lambda: datetime.now(UTC)

async def activate_subscription(bot: Bot, session: AsyncSession, user: User) -> str:
    now = datetime.now(tz=UTC).replace(tzinfo=None)
    # Если продлеваем — дата старта не меняется, переносим конец
    if user.end_date and user.end_date > now:
        user.end_date = user.end_date + timedelta(days=30)
        # start_date оставляем
    else:
        user.start_date = now
        user.end_date = now + timedelta(days=30)

    user.status = "ACTIVE"
    await session.commit()

    # Создаём одноразовую ссылку в канал
    # member_limit=1, срок жизни 1 день (чтобы ссылка не утекала)
    invite = await bot.create_chat_invite_link(
        chat_id=settings.CHANNEL_ID,
        member_limit=1,
        expire_date=int((datetime.utcnow() + timedelta(days=1)).timestamp())
    )
    return invite.invite_link

async def deactivate_subscription(session: AsyncSession, user: User) -> None:
    user.status = "INACTIVE"
    await session.commit()

def remaining_days(user: User) -> Optional[int]:
    if not user.end_date:
        return None
    now = datetime.utcnow()
    diff = user.end_date - now
    return max(0, diff.days)

