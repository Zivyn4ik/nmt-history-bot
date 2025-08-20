from __future__ import annotations
from datetime import datetime, timedelta, timezone
from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from sqlalchemy import update, select
from .db import Session, User, Subscription
from .config import settings

UTC = timezone.utc
NOW = lambda: datetime.now(UTC)

async def ensure_user(tg_user) -> None:
    async with Session() as s:
        exists = await s.get(User, tg_user.id)
        if exists:
            if exists.username != tg_user.username:
                exists.username = tg_user.username
                await s.commit()
            return
        s.add(User(id=tg_user.id, username=tg_user.username))
        await s.commit()

async def get_or_create_subscription(user_id: int) -> Subscription:
    async with Session() as s:
        sub = await s.get(Subscription, user_id)
        if sub is None:
            sub = Subscription(user_id=user_id, status="expired")
            s.add(sub)
            await s.commit()
            await s.refresh(sub)
        return sub

async def update_subscription(user_id: int, **kwargs) -> None:
    async with Session() as s:
        await s.execute(update(Subscription).where(Subscription.user_id==user_id).values(**kwargs))
        await s.commit()

async def get_subscription_status(user_id: int) -> Subscription:
    async with Session() as s:
        sub = await s.get(Subscription, user_id)
        if sub is None:
            sub = Subscription(user_id=user_id, status="expired")
            s.add(sub)
            await s.commit()
            await s.refresh(sub)
        return sub

async def has_active_access(user_id: int) -> bool:
    sub = await get_subscription_status(user_id)
    if sub.status == "active" and sub.paid_until and sub.paid_until > NOW():
        return True
    if sub.status == "grace" and sub.grace_until and sub.grace_until > NOW():
        return True
    return False

async def send_one_time_invite(bot: Bot, user_id: int):
    from aiogram.methods import CreateChatInviteLink
    expire = int((NOW() + timedelta(days=3)).timestamp())
    link = await bot(CreateChatInviteLink(chat_id=settings.CHANNEL_ID,
                                          expire_date=expire,
                                          member_limit=1,
                                          creates_join_request=False))
    await bot.send_message(user_id, f"Ваше запрошення до каналу: {link.invite_link}\n(Діє 3 дні, одноразове)")

async def activate_or_extend(bot: Bot, user_id: int, months: int = 1):
    sub = await get_or_create_subscription(user_id)
    start = max(NOW(), sub.paid_until or NOW())
    paid_until = start + timedelta(days=30*months)
    grace_until = paid_until + timedelta(days=3)
    await update_subscription(user_id,
                              status="active",
                              paid_until=paid_until,
                              grace_until=grace_until,
                              updated_at=NOW())
    await send_one_time_invite(bot, user_id)
    await bot.send_message(user_id, f"Підписка активна до {paid_until:%Y-%m-%d}. Дякуємо за оплату!")

async def enforce_expirations(bot: Bot):
    now = NOW()
    today = now.date()

    async with Session() as s:
        res = await s.execute(select(Subscription))
        subs = res.scalars().all()

    for sub in subs:
        if sub.status == "active" and sub.paid_until:
            reminder_start = sub.paid_until - timedelta(days=3)
            in_window = reminder_start.date() <= today <= sub.paid_until.date()
            if in_window and sub.last_reminded_on != today:
                try:
                    await bot.send_message(sub.user_id, "Нагадування: підписка закінчується. Продовжте через /buy.")
                except Exception:
                    pass
                await update_subscription(sub.user_id, last_reminded_on=today, updated_at=now)

        if sub.status == "active" and sub.paid_until and now > sub.paid_until:
            await update_subscription(sub.user_id, status="grace", updated_at=now)

        if sub.status == "grace" and sub.grace_until:
            if now <= sub.grace_until:
                if sub.last_reminded_on != today:
                    try:
                        days_left = max((sub.grace_until.date() - today).days, 0)
                        await bot.send_message(sub.user_id, f"Оплату прострочено. Залишилось днів: {days_left}. Оплатіть через /buy.")
                    except Exception:
                        pass
                    await update_subscription(sub.user_id, last_reminded_on=today, updated_at=now)
            else:
                try:
                    await bot.ban_chat_member(settings.CHANNEL_ID, sub.user_id)
                    await bot.unban_chat_member(settings.CHANNEL_ID, sub.user_id)
                except TelegramBadRequest:
                    pass
                await update_subscription(sub.user_id, status="expired", updated_at=now)
                try:
                    await bot.send_message(sub.user_id, "Термін оплати минув. Доступ закрито. Оплатіть через /buy, щоб відновити.")
                except Exception:
                    pass
