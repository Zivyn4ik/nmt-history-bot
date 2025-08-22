# bot/services.py
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
class SubStatus:
    status: str | None     # "active" | "expired" | None
    paid_until: date | None


# -------- USERS --------
async def ensure_user(tg_user) -> None:
    """
    Безопасно создаём пользователя только с id (как в вашем архиве).
    Никаких лишних колонок не трогаем.
    """
    async with Session() as s:
        dbu = await s.get(User, tg_user.id)
        if dbu is None:
            s.add(User(id=tg_user.id))
            await s.commit()


# -------- SUBSCRIPTIONS --------
async def get_subscription_status(user_id: int) -> SubStatus:
    async with Session() as s:
        res = await s.execute(select(Subscription).where(Subscription.user_id == user_id))
        sub = res.scalar_one_or_none()
        if not sub:
            return SubStatus(status=None, paid_until=None)

        is_active = bool(sub.paid_until and sub.paid_until >= now().date())
        return SubStatus(
            status="active" if is_active else "expired",
            paid_until=sub.paid_until,
        )


async def has_active_access(user_id: int) -> bool:
    st = await get_subscription_status(user_id)
    return st.status == "active"


async def update_subscription(user_id: int, **fields) -> None:
    async with Session() as s:
        await s.execute(
            update(Subscription)
            .where(Subscription.user_id == user_id)
            .values(**fields)
        )
        await s.commit()


async def activate_or_extend(bot: Bot, user_id: int, months: int = 1) -> None:
    """
    Активируем/продлеваем ПРИШЕДШУЮ оплату (вызывается из callback WFP).
    Только тут отсылаем «Підписка активна до…».
    """
    async with Session() as s:
        res = await s.execute(select(Subscription).where(Subscription.user_id == user_id))
        sub = res.scalar_one_or_none()
        today = now().date()

        if sub and sub.paid_until and sub.paid_until >= today:
            start_from = sub.paid_until + timedelta(days=1)
        else:
            start_from = today

        new_until = start_from + timedelta(days=30 * months)

        if not sub:
            sub = Subscription(
                user_id=user_id,
                status="active",
                paid_until=new_until,
                updated_at=now(),
            )
            s.add(sub)
        else:
            sub.status = "active"
            sub.paid_until = new_until
            sub.updated_at = now()

        await s.commit()

    # Сообщение пользователю и попытка авто-апрува join-request
    try:
        await bot.send_message(
            user_id,
            f"✅ Оплату підтверджено. Підписка активна до {new_until:%Y-%m-%d}.\n"
            f"Тисніть, щоб увійти: {settings.TG_JOIN_REQUEST_URL}",
        )
    except Exception:
        pass

    try:
        await bot.approve_chat_join_request(settings.CHANNEL_ID, user_id)
    except TelegramBadRequest:
        pass
    except Exception:
        pass


async def enforce_expirations(bot: Bot) -> None:
    """
    Плановая проверка окончаний — без лишних сообщений пользователю.
    """
    moment = now()
    today = moment.date()

    async with Session() as s:
        res = await s.execute(select(Subscription))
        subs = list(res.scalars())

    for sub in subs:
        if sub.status == "active" and sub.paid_until and sub.paid_until < today:
            await update_subscription(sub.user_id, status="expired", updated_at=moment)
