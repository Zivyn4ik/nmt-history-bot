
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone, date
from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from sqlalchemy import update, select
from .db import Session, User, Subscription
from .config import settings

UTC = timezone.utc
NOW = lambda: datetime.now(UTC)

@dataclass
class SubInfo:
    status: str
    paid_until: datetime | None

async def ensure_user(tg_user) -> None:
    async with Session() as s:
        exists = await s.get(User, tg_user.id)
        if exists:
            if exists.username != tg_user.username:
                exists.username = tg_user.username
                await s.commit()
        else:
            s.add(User(id=tg_user.id, username=tg_user.username))
            # создадим подписку по умолчанию
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

async def update_subscription(user_id: int, **fields):
    async with Session() as s:
        await s.execute(update(Subscription).where(Subscription.user_id == user_id).values(**fields))
        await s.commit()

async def has_active_access(user_id: int) -> bool:
    async with Session() as s:
        sub = await s.get(Subscription, user_id)
        if not sub or sub.status != "active" or not sub.paid_until:
            return False
        now = NOW()
        if now <= sub.paid_until + timedelta(days=3):
            return True
        return False

async def activate_or_extend(bot: Bot, user_id: int) -> None:
    """Активирует или продлевает подписку на 30 дней и высылает приглашение."""
    async with Session() as s:
        sub = await s.get(Subscription, user_id)
        if not sub:
            sub = Subscription(user_id=user_id, status="expired")
            s.add(sub)
            await s.flush()

        now = NOW()
        base = sub.paid_until if (sub.paid_until and sub.paid_until > now) else now
        new_until = base + timedelta(days=30)
        sub.status = "active"
        sub.paid_until = new_until
        sub.grace_until = new_until + timedelta(days=3)
        sub.updated_at = now
        await s.commit()

    # try approve join request automatically or send invite link
    try:
        # одобрим заявки на вступление, если есть
        # (aiogram автоматически обрабатывает через on_join, поэтому тут просто продублируем приглашение)
        link = await bot.create_chat_invite_link(settings.CHANNEL_ID, creates_join_request=False, expire_date=None, member_limit=1)
        try:
            await bot.send_message(user_id, "Підписка активна до <b>{}</b>. Тисніть щоб увійти: {}".format(new_until.date(), link.invite_link))
        except Exception:
            pass
    except TelegramBadRequest:
        # если нет прав на инвайт — просто помолчим
        pass

async def enforce_expirations(bot: Bot):
    """Ежедневные напоминания за 3 дня до окончания и блокировка после grace-периода."""
    today = date.today()
    now = NOW()

    async with Session() as s:
        res = await s.execute(select(Subscription))
        subs = res.scalars().all()

    for sub in subs:
        # Напоминания за 3 дня до конца
        if sub.status == "active" and sub.paid_until:
            reminder_start = sub.paid_until - timedelta(days=3)
            in_window = reminder_start.date() <= today <= sub.paid_until.date()
            if in_window and getattr(sub, "last_reminded_on", None) != today:
                try:
                    await bot.send_message(sub.user_id, "Нагадування: підписка закінчується. Продовжте через /buy.")
                except Exception:
                    pass
                await update_subscription(sub.user_id, last_reminded_on=today, updated_at=now)

        # Блокировка после grace 3 дня
        if sub.status == "active" and sub.paid_until and now > (sub.paid_until + timedelta(days=3)):
            # отзываем доступ
            try:
                await bot.ban_chat_member(settings.CHANNEL_ID, sub.user_id)  # удалим из канала
                await bot.unban_chat_member(settings.CHANNEL_ID, sub.user_id)  # сразу разблокируем, чтобы мог вернуться после оплаты
            except Exception:
                pass
            await update_subscription(sub.user_id, status="expired", updated_at=now)
