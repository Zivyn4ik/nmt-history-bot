from datetime import datetime, timedelta, timezone
from typing import Optional
import logging
from aiogram import Bot
from bot.config import settings
from bot.db import User, AsyncSession

log = logging.getLogger(__name__)
UTC = timezone.utc

async def activate_subscription(bot: Bot, session: AsyncSession, user: User) -> str:
    now = datetime.now(tz=UTC).replace(tzinfo=None)
    if user.end_date and user.end_date > now:
        user.end_date += timedelta(days=30)
    else:
        user.start_date = now
        user.end_date = now + timedelta(days=30)
    user.status = "ACTIVE"
    await session.commit()

    invite = await bot.create_chat_invite_link(
        chat_id=settings.CHANNEL_ID,
        member_limit=1,
        expire_date=int((datetime.utcnow() + timedelta(days=1)).timestamp())
    )
    return invite.invite_link

async def deactivate_subscription(session: AsyncSession, user: User):
    user.status = "INACTIVE"
    await session.commit()

def remaining_days(user: User) -> Optional[int]:
    if not user.end_date:
        return None
    diff = user.end_date - datetime.utcnow()
    return max(0, diff.days)
