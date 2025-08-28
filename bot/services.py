from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone, date
from typing import Optional
import logging

from aiogram import Bot
from aiogram.enums.chat_member_status import ChatMemberStatus
from sqlalchemy import select, update

from bot.db import Session, User, Subscription
from bot.config import settings

log = logging.getLogger(__name__)
UTC = timezone.utc
now = lambda: datetime.now(UTC)


@dataclass
class SubInfo:
    status: str
    paid_until: datetime | None


async def ensure_user(tg_user) -> None:
    async with Session() as s:
        obj = await s.get(User, tg_user.id)
        if obj:
            if tg_user.username and obj.username != tg_user.username:
                obj.username = tg_user.username
                await s.commit()
        else:
            s.add(User(id=tg_user.id, username=tg_user.username))
            s.add(Subscription(user_id=tg_user.id, status="expired"))
            await s.commit()


def _tz_aware_utc(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


async def get_subscription_status(user_id: int) -> SubInfo:
    async with Session() as s:
        sub = await s.get(Subscription, user_id)
        if not sub:
            sub = Subscription(user_id=user_id, status="expired")
            s.add(sub)
            await s.commit()
        return SubInfo(status=sub.status, paid_until=_tz_aware_utc(sub.paid_until))


async def activate_or_extend(bot: Bot, user_id: int) -> None:
    """
    Активирует или продлевает подписку на 30 дней.
    Отправляет join-link в канал.
    """
    async with Session() as s:
        sub = await s.get(Subscription, user_id)
        if not sub:
            sub = Subscription(user_id=user_id, status="expired")
            s.add(sub)
            await s.flush()

        current = now()
        base = _tz_aware_utc(sub.paid_until) or current
        if base < current:
            base = current

        new_until = base + timedelta(days=30)
        sub.status = "active"
        sub.paid_until = _tz_aware_utc(new_until)
        sub.grace_until = _tz_aware_utc(new_until + timedelta(days=3))
        sub.updated_at = current
        await s.commit()

    try:
        # попытка автоматически принять join request
        await bot.approve_chat_join_request(settings.CHANNEL_ID, user_id)
    except Exception:
        pass

    try:
        # отправка приглашения через create_chat_invite_link
        expire_ts = int(now().timestamp()) + 3 * 24 * 60 * 60
        link_obj = await bot.create_chat_invite_link(
            chat_id=settings.CHANNEL_ID,
            name=f"joinreq-{user_id}-{int(now().timestamp())}",
            expire_date=expire_ts,
            member_limit=1,
            creates_join_request=True,
        )
        invite = link_obj.invite_link
        await bot.send_message(
            user_id,
            f"✅ Ваша підписка активна до <b>{new_until.date()}</b>.\n"
            f"Натисніть, щоб подати заявку на вступ до каналу:\n{invite}",
            parse_mode="HTML",
        )
    except Exception as e:
        log.warning("Cannot send subscription message to user %s: %s", user_id, e)
