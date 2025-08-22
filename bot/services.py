from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone, date

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from sqlalchemy import select, update

from .db import Session, User, Subscription
from .config import settings

UTC = timezone.utc
now = lambda: datetime.now(UTC)

@dataclass
class SubInfo:
    status: str
    paid_until: datetime | None

async def ensure_user(tg_user) -> None:
    """Создаёт пользователя, если его ещё нет."""
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

async def get_subscription_status(user_id: int) -> SubInfo:
    async with Session() as s:
        sub = await s.get(Subscription, user_id)
        if not sub:
            sub = Subscription(user_id=user_id, status="expired")
            s.add(sub)
            await s.commit()
        return SubInfo(status=sub.status, paid_until=sub.paid_until)

async def update_subscription(user_id: int, **fields) -> None:
    async with Session() as s:
        await s.execute(
            update(Subscription).where(Subscription.user_id == user_id).values(**fields)
        )
        await s.commit()

async def has_active_access(user_id: int) -> bool:
    async with Session() as s:
        sub = await s.get(Subscription, user_id)
        if not sub or sub.status != "active" or not sub.paid_until:
            return False
        return now() <= sub.paid_until + timedelta(days=3)

async def activate_or_extend(bot: Bot, user_id: int) -> None:
    """Активирует или продлевает подписку на 30 дней и отправляет ссылку в канал."""
    async with Session() as s:
        sub = await s.get(Subscription, user_id)
        if not sub:
            sub = Subscription(user_id=user_id, status="expired")
            s.add(sub)
            await s.flush()

        current = now()
        base = sub.paid_until if (sub.paid_until and sub.paid_until > current) else current
        new_until = base + timedelta(days=30)

        sub.status = "active"
        sub.paid_until = new_until
        sub.grace_until = new_until + timedelta(days=3)
        sub.updated_at = current
        await s.commit()

    # Пытаемся одобрить заявку, если она уже есть
    try:
        await bot.approve_chat_join_request(settings.CHANNEL_ID, user_id)
    except Exception:
        pass

    # И всё равно отправим ссылку
    try:
        await bot.send_message(
            user_id,
            f"Підписка активна до <b>{new_until.date()}</b>. Тисніть щоб увійти:\n{settings.TG_JOIN_REQUEST_URL}",
        )
    except Exception:
        pass

async def enforce_expirations(bot: Bot) -> None:
    """Ежедневные напоминания и отключение после grace-периода."""
    today = date.today()
    moment = now()

    async with Session() as s:
        res = await s.execute(select(Subscription))
        for sub in res.scalars().all():
            # напоминание за 3 дня
            if (
                sub.status == "active"
                and sub.paid_until
                and (sub.paid_until - timedelta(days=3)).date() <= today
                and sub.last_reminded_on != today
            ):
                try:
                    await bot.send_message(
                        sub.user_id,
                        "Нагадування: підписка закінчується за 3 дні. Продовжте через /buy.",
                    )
                except Exception:
                    pass
                await update_subscription(
                    sub.user_id, last_reminded_on=today, updated_at=moment
                )

            # отключение после grace (3 дня)
            if (
                sub.status == "active"
                and sub.paid_until
                and moment > (sub.paid_until + timedelta(days=3))
            ):
                try:
                    await bot.ban_chat_member(settings.CHANNEL_ID, sub.user_id)
                    await bot.unban_chat_member(settings.CHANNEL_ID, sub.user_id)
                except Exception:
                    pass
                await update_subscription(sub.user_id, status="expired", updated_at=moment)
