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


# ----------------- USERS -----------------

async def ensure_user(tg_user) -> None:
    async with Session() as s:
        dbu = await s.get(User, tg_user.id)
        if dbu is None:
            dbu = User(
                id=tg_user.id,
                first_name=tg_user.first_name or "",
                last_name=tg_user.last_name or "",
                username=tg_user.username or "",
            )
            s.add(dbu)
            await s.commit()
        else:
            # лёгкое обновление профиля
            dbu.first_name = tg_user.first_name or dbu.first_name
            dbu.last_name = tg_user.last_name or dbu.last_name
            dbu.username = tg_user.username or dbu.username
            await s.commit()


# ----------------- SUBSCRIPTIONS -----------------

async def get_subscription_status(user_id: int) -> SubStatus:
    async with Session() as s:
        res = await s.execute(select(Subscription).where(Subscription.user_id == user_id))
        sub = res.scalar_one_or_none()
        if not sub:
            return SubStatus(status=None, paid_until=None)

        # "active" только если есть paid_until и оно не в прошлом
        is_active = sub.paid_until and sub.paid_until >= now().date()
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
    Активирует или продлевает подписку после подтвержденной оплаты.
    Тут же отправляем пользователю корректное сообщение и ссылку.
    """
    async with Session() as s:
        res = await s.execute(select(Subscription).where(Subscription.user_id == user_id))
        sub = res.scalar_one_or_none()
        today = now().date()

        # новая оплата: если подписка ещё активна, продлеваем от paid_until,
        # иначе — от сегодняшней даты
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

    # отправляем ссылку и пробуем одобрить join-request, если висит
    try:
        await bot.send_message(
            user_id,
            f"✅ Оплату підтверджено. Підписка активна до {new_until:%Y-%m-%d}.\nТисніть, щоб увійти: {settings.TG_JOIN_REQUEST_URL}",
        )
    except Exception:
        pass

    try:
        await bot.approve_chat_join_request(settings.CHANNEL_ID, user_id)  # если есть запрос — одобрится
    except TelegramBadRequest:
        pass
    except Exception:
        pass


async def enforce_expirations(bot: Bot) -> None:
    """
    Ежедневно приводим статусы в порядок и вычищаем доступы,
    у которых закончился grace-период (3 дня).
    Никаких сообщений “активна до …” здесь не шлём!
    """
    moment = now()
    today = moment.date()

    async with Session() as s:
        res = await s.execute(select(Subscription))
        subs = list(res.scalars())

    for sub in subs:
        # истекла — переводим в expired
        if sub.status == "active" and sub.paid_until and sub.paid_until < today:
            await update_subscription(sub.user_id, status="expired", updated_at=moment)

        # жёсткое выключение после grace
        if (
            sub.status == "active"
            and sub.paid_until
            and moment > (datetime.combine(sub.paid_until, datetime.min.time(), tzinfo=UTC) + timedelta(days=3))
        ):
            try:
                await bot.ban_chat_member(settings.CHANNEL_ID, sub.user_id)
                await bot.unban_chat_member(settings.CHANNEL_ID, sub.user_id)
            except Exception:
                pass
            await update_subscription(sub.user_id, status="expired", updated_at=moment)
