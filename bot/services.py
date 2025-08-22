from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone, date
from typing import Optional

from aiogram import Bot
from aiogram.enums.chat_member_status import ChatMemberStatus
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


def _tz_aware_utc(dt: Optional[datetime]) -> Optional[datetime]:
    """Возвращает datetime aware в UTC (если было naive — помечаем как UTC)."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


async def get_subscription_status(user_id: int) -> SubInfo:
    """
    Возвращает статус подписки с paid_until, гарантированно timezone-aware (UTC).
    Это устраняет ситуацию, когда на кнопке «проверка статуса» всегда пишет «нет подписки»
    из-за сравнения naive- и aware-datetime.
    """
    async with Session() as s:
        sub = await s.get(Subscription, user_id)
        if not sub:
            sub = Subscription(user_id=user_id, status="expired")
            s.add(sub)
            await s.commit()
        return SubInfo(status=sub.status, paid_until=_tz_aware_utc(sub.paid_until))


async def update_subscription(user_id: int, **fields) -> None:
    # Нормализуем датовые поля в UTC, если их передают извне
    if "paid_until" in fields:
        fields["paid_until"] = _tz_aware_utc(fields["paid_until"])
    if "grace_until" in fields:
        fields["grace_until"] = _tz_aware_utc(fields["grace_until"])
    if "updated_at" in fields:
        fields["updated_at"] = _tz_aware_utc(fields["updated_at"])

    async with Session() as s:
        await s.execute(
            update(Subscription).where(Subscription.user_id == user_id).values(**fields)
        )
        await s.commit()


async def has_active_access(user_id: int) -> bool:
    """
    Проверка «можно ли держать пользователя в канале».
    Здесь учитываем 3 дня grace-периода (как у тебя было).
    """
    async with Session() as s:
        sub = await s.get(Subscription, user_id)
        if not sub or sub.status != "active" or not sub.paid_until:
            return False
        paid_until = _tz_aware_utc(sub.paid_until)
        return now() <= paid_until + timedelta(days=3)


async def is_member_of_channel(bot: Bot, channel_id: int, user_id: int) -> bool:
    """
    Фактическая проверка членства в канале.
    Важно: channel_id должен быть числом формата -100xxxxxxxxxx, не @username.
    """
    try:
        member = await bot.get_chat_member(chat_id=channel_id, user_id=user_id)
        return member.status in {
            ChatMemberStatus.OWNER,
            ChatMemberStatus.ADMINISTRATOR,
            ChatMemberStatus.MEMBER,
        }
    except Exception:
        return False


async def activate_or_extend(bot: Bot, user_id: int) -> None:
    """Активирует или продлевает подписку на 30 дней и отправляет ссылку в канал."""
    async with Session() as s:
        sub = await s.get(Subscription, user_id)
        if not sub:
            sub = Subscription(user_id=user_id, status="expired")
            s.add(sub)
            await s.flush()

        current = now()
        # если подписка ещё действует — продлеваем от её конца, иначе от текущего момента
        base = _tz_aware_utc(sub.paid_until) if sub.paid_until else current
        if base < current:
            base = current

        new_until = base + timedelta(days=30)

        sub.status = "active"
        sub.paid_until = _tz_aware_utc(new_until)
        sub.grace_until = _tz_aware_utc(new_until + timedelta(days=3))
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
            f"Підписка активна до <b>{new_until.date()}</b>. "
            f"Натисніть, щоб увійти до каналу:\n{settings.TG_JOIN_REQUEST_URL}",
            parse_mode="HTML",
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
            paid_until = _tz_aware_utc(sub.paid_until)

            # напоминание за 3 дня
            if (
                sub.status == "active"
                and paid_until
                and (paid_until - timedelta(days=3)).date() <= today
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
                and paid_until
                and moment > (paid_until + timedelta(days=3))
            ):
                try:
                    # мягкое удаление: бан + разбан
                    await bot.ban_chat_member(settings.CHANNEL_ID, sub.user_id)
                    await bot.unban_chat_member(settings.CHANNEL_ID, sub.user_id)
                except Exception:
                    pass
                await update_subscription(sub.user_id, status="expired", updated_at=moment)
