from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone, date
from typing import Optional

from aiogram import Bot
from aiogram.enums.chat_member_status import ChatMemberStatus
from sqlalchemy import select, update

from .db import Session, User, Subscription
from .config import settings

log = logging.getLogger("services")

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

def _tz(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is None:
        return None
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)

async def get_subscription_status(user_id: int) -> SubInfo:
    async with Session() as s:
        sub = await s.get(Subscription, user_id)
        if not sub:
            sub = Subscription(user_id=user_id, status="expired")
            s.add(sub); await s.commit()
        return SubInfo(status=sub.status, paid_until=_tz(sub.paid_until))

async def update_subscription(user_id: int, **fields) -> None:
    for k in ("paid_until", "grace_until", "updated_at"):
        if k in fields:
            fields[k] = _tz(fields[k])
    async with Session() as s:
        await s.execute(update(Subscription).where(Subscription.user_id == user_id).values(**fields))
        await s.commit()

async def has_active_access(user_id: int) -> bool:
    async with Session() as s:
        sub = await s.get(Subscription, user_id)
        if not sub or sub.status != "active" or not sub.paid_until:
            return False
        return now() <= _tz(sub.paid_until) + timedelta(days=3)

async def is_member_of_channel(bot: Bot, channel_id: int, user_id: int) -> bool:
    try:
        m = await bot.get_chat_member(chat_id=channel_id, user_id=user_id)
        return m.status in {ChatMemberStatus.OWNER, ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.MEMBER}
    except Exception:
        return False

async def _create_join_request_link(bot: Bot, user_id: int) -> str:
    expire = int(now().timestamp()) + 3 * 24 * 60 * 60
    link = await bot.create_chat_invite_link(
        chat_id=settings.CHANNEL_ID,
        name=f"joinreq-{user_id}-{int(now().timestamp())}",
        expire_date=expire,
        member_limit=1,
        creates_join_request=True,
    )
    return link.invite_link

async def activate_or_extend(bot: Bot, user_id: int) -> None:
    """Активирует/продлевает на 30 дней, пытается одобрить заявку, шлёт join-request ссылку."""
    async with Session() as s:
        sub = await s.get(Subscription, user_id)
        if not sub:
            sub = Subscription(user_id=user_id, status="expired")
            s.add(sub); await s.flush()

        current = now()
        base = _tz(sub.paid_until) or current
        if base < current:
            base = current

        new_until = base + timedelta(days=30)
        sub.status = "active"
        sub.paid_until = _tz(new_until)
        sub.grace_until = _tz(new_until + timedelta(days=3))
        sub.updated_at = current
        await s.commit()
        log.info("SUB UPDATED user_id=%s status=%s paid_until=%s", user_id, sub.status, sub.paid_until)

    try:
        await bot.approve_chat_join_request(settings.CHANNEL_ID, user_id)
        log.info("JOIN REQUEST APPROVED user_id=%s", user_id)
    except Exception:
        pass

    try:
        invite = await _create_join_request_link(bot, user_id)
        await bot.send_message(
            user_id,
            f"Підписка активна до <b>{new_until.date()}</b>.\n"
            f"Натисніть, щоб подати заявку на вступ:\n{invite}",
            parse_mode="HTML",
        )
        log.info("JOIN LINK SENT user_id=%s", user_id)
    except Exception:
        log.exception("SEND LINK failed user_id=%s", user_id)

async def enforce_expirations(bot: Bot) -> None:
    today = date.today()
    moment = now()
    async with Session() as s:
        res = await s.execute(select(Subscription))
        for sub in res.scalars().all():
            pu = _tz(sub.paid_until)
            if sub.status == "active" and pu and (pu - timedelta(days=3)).date() <= today and sub.last_reminded_on != today:
                try:
                    await bot.send_message(sub.user_id, "Нагадування: підписка закінчується за 3 дні. Продовжте через /buy.")
                except Exception:
                    pass
                await update_subscription(sub.user_id, last_reminded_on=today, updated_at=moment)

            if sub.status == "active" and pu and moment > (pu + timedelta(days=3)):
                try:
                    await bot.ban_chat_member(settings.CHANNEL_ID, sub.user_id)
                    await bot.unban_chat_member(settings.CHANNEL_ID, sub.user_id)
                except Exception:
                    pass
                await update_subscription(sub.user_id, status="expired", updated_at=moment)
