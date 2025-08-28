from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, timezone, date
from typing import Optional
import logging
from sqlalchemy import select, update
from aiogram import Bot
from bot.db import Session, User, Subscription

log = logging.getLogger(__name__)
UTC = timezone.utc
now = lambda: datetime.now(UTC)

@dataclass
class SubInfo:
    status: str
    paid_until: Optional[datetime]

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

async def update_subscription(user_id: int, **fields) -> None:
    for key in ["paid_until", "grace_until", "updated_at"]:
        if key in fields:
            fields[key] = _tz_aware_utc(fields[key])
    async with Session() as s:
        await s.execute(update(Subscription).where(Subscription.user_id == user_id).values(**fields))
        await s.commit()

async def has_active_access(user_id: int) -> bool:
    async with Session() as s:
        sub = await s.get(Subscription, user_id)
        if not sub or sub.status not in {"active", "grace"}:
            return False
        paid_until = _tz_aware_utc(sub.paid_until)
        grace_until = _tz_aware_utc(sub.grace_until)
        if not paid_until:
            return False
        return now() <= (grace_until or paid_until)

async def enforce_expirations(bot: Bot):
    moment = now()
    async with Session() as s:
        res = await s.execute(select(Subscription))
        subs = res.scalars().all()
        for sub in subs:
            paid_until = _tz_aware_utc(sub.paid_until)
            if paid_until and paid_until < moment:
                sub.status = "expired"
        await s.commit()
