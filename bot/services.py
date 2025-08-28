from datetime import datetime, timedelta, timezone
from typing import Optional
import logging
from aiogram import Bot
from aiogram.enums.chat_member_status import ChatMemberStatus
from bot.config import settings
from bot.db import User, AsyncSession

log = logging.getLogger(__name__)
UTC = timezone.utc


async def activate_subscription(bot: Bot, session: AsyncSession, user: User) -> str:
    now = datetime.now(tz=UTC).replace(tzinfo=None)
    # Продление или новая подписка
    if user.end_date and user.end_date > now:
        user.end_date += timedelta(days=30)
    else:
        user.start_date = now
        user.end_date = now + timedelta(days=30)

    user.status = "ACTIVE"
    await session.commit()

    invite_link = await get_invite_link(bot, user.id)
    return invite_link or "Вы уже в канале, ссылка не требуется."


async def deactivate_subscription(session: AsyncSession, user: User):
    user.status = "INACTIVE"
    await session.commit()


def remaining_days(user: User) -> Optional[int]:
    if not user.end_date:
        return None
    diff = user.end_date - datetime.utcnow()
    return max(0, diff.days)


async def get_invite_link(bot: Bot, user_id: int):
    member = await bot.get_chat_member(chat_id=settings.CHANNEL_ID, user_id=user_id)
    if member.status in (ChatMemberStatus.MEMBER, ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER):
        return None  # Уже в канале, ссылка не нужна
    invite = await bot.create_chat_invite_link(
        chat_id=settings.CHANNEL_ID,
        member_limit=1,
        expire_date=int((datetime.utcnow() + timedelta(days=1)).timestamp())
    )
    return invite.invite_link


# ----------------- Фоновая задача для APScheduler -----------------
async def check_subscriptions(bot: Bot, async_session_maker):
    async with async_session_maker() as session:
        result = await session.execute(User.__table__.select())
        users = result.fetchall()
        for row in users:
            user = User(**dict(row))
            if user.status == "ACTIVE" and user.end_date:
                days_left = remaining_days(user)
                if days_left == 3:
                    await bot.send_message(user.id, "⚠️ Ваша подписка заканчивается через 3 дня. Продлите её.")
                elif days_left <= 0:
                    await deactivate_subscription(session, user)
                    await bot.send_message(user.id, "❌ Подписка закончилась. Доступ к каналу закрыт.")
