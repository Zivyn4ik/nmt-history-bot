from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone, date
from typing import Optional

from aiogram import Bot
from aiogram.enums.chat_member_status import ChatMemberStatus
from sqlalchemy import select, update

from bot.db import Session, User, Subscription
from bot.config import settings, Subscription

import logging
log = logging.getLogger(__name__)

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
    """Сделать datetime timezone-aware (UTC)."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


async def get_subscription_status(user_id: int) -> SubInfo:
    """Вернуть статус подписки и paid_until (UTC-aware)."""
    async with Session() as s:
        sub = await s.get(Subscription, user_id)
        if not sub:
            sub = Subscription(user_id=user_id, status="expired")
            s.add(sub)
            await s.commit()
        return SubInfo(status=sub.status, paid_until=_tz_aware_utc(sub.paid_until))


async def update_subscription(user_id: int, **fields) -> None:
    """Обновить поля подписки (нормализуя даты в UTC)."""
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
    """Можно ли держать пользователя в канале (active или grace)."""
    async with Session() as s:
        sub = await s.get(Subscription, user_id)
        if not sub:
            return False
        if sub.status not in {"active", "grace"}:
            return False

        paid_until = _tz_aware_utc(sub.paid_until)
        grace_until = _tz_aware_utc(sub.grace_until)

        if not paid_until:
            return False

        # пока не вышли за пределы grace
        return now() <= (grace_until or paid_until)


async def is_member_of_channel(bot: Bot, channel_id: int, user_id: int) -> bool:
    """Фактическая проверка членства в канале."""
    try:
        m = await bot.get_chat_member(chat_id=channel_id, user_id=user_id)
        return m.status in {
            ChatMemberStatus.OWNER,
            ChatMemberStatus.ADMINISTRATOR,
            ChatMemberStatus.MEMBER,
        }
    except Exception:
        return False


async def create_join_request_link(bot: Bot, user_id: int) -> str:
    """
    Сгенерировать ПРАВИЛЬНУЮ ссылку: с запросом на вступление (не мгновенную).
    Делает персональную ссылку с лимитом 1 и коротким TTL, чтобы её нельзя было «утянуть».
    """
    # короткий срок жизни ссылки (например, 3 дня)
    expire_ts = int(now().timestamp()) + 3 * 24 * 60 * 60

    link_obj = await bot.create_chat_invite_link(
        chat_id=settings.CHANNEL_ID,
        name=f"joinreq-{user_id}-{int(now().timestamp())}",
        expire_date=expire_ts,          # истекает быстро
        member_limit=1,                 # не более 1 вступления по ссылке
        creates_join_request=True,      # КЛЮЧЕВОЕ: нужна заявка на вступление
    )
    return link_obj.invite_link


async def activate_or_extend(bot: Bot, user_id: int) -> None:
    """
    Активирует или продлевает подписку на 30 дней.
    Обновляет статус в БД и отправляет пользователю сообщение с join-request ссылкой.
    """
    async with Session() as s:
        sub = await s.get(Subscription, user_id)
        if not sub:
            sub = Subscription(user_id=user_id, status="expired")
            s.add(sub)
            await s.flush()

        current = now()
        base = _tz_aware_utc(sub.paid_until) if sub.paid_until else current
        if base < current:
            base = current

        new_until = base + timedelta(days=30)

        sub.status = "active"
        sub.paid_until = _tz_aware_utc(new_until)
        sub.grace_until = _tz_aware_utc(new_until + timedelta(days=3))
        sub.updated_at = current
        await s.commit()

    # Одобряем заявку, если пользователь уже подал join-request
    try:
        await bot.approve_chat_join_request(settings.CHANNEL_ID, user_id)
    except Exception:
        pass

    # Отправляем сообщение с join-request ссылкой
    try:
        invite = await create_join_request_link(bot, user_id)
        await bot.send_message(
            user_id,
            f"✅ Ваша підписка активна до <b>{new_until.date()}</b>.\n"
            f"Натисніть, щоб подати заявку на вступ до каналу:\n{invite}",
            parse_mode="HTML",
        )
    except Exception as e:
        log.warning("Cannot send subscription message to user %s: %s", user_id, e)



async def enforce_expirations(bot: Bot) -> None:
    """Ежедневная проверка подписок:
    - напоминание за 3 дня до окончания
    - уведомление в день окончания (с кнопкой продления)
    - статус grace на 3 дня
    - отключение по истечении grace
    - зачистка «левых» пользователей
    """
    today = date.today()
    moment = now()

    async with Session() as s:
        res = await s.execute(select(Subscription))
        subs = res.scalars().all()

    for sub in subs:
        paid_until = _tz_aware_utc(sub.paid_until)
        grace_until = _tz_aware_utc(sub.grace_until)

        # --- Напоминание за 3 дня ---
        if (
            sub.status == "active"
            and paid_until
            and (paid_until - timedelta(days=3)).date() == today
            and sub.last_reminded_on != today
        ):
            try:
                await bot.send_message(
                    sub.user_id,
                    "⚠️ Нагадування: ваша підписка закінчується через 3 дні. "
                    "Продліть її через /buy.",
                )
            except Exception:
                pass
            await update_subscription(sub.user_id, last_reminded_on=today, updated_at=moment)

        # --- Уведомление в день окончания ---
        if (
            sub.status == "active"
            and paid_until
            and paid_until.date() == today
            and sub.last_reminded_on != today
        ):
            try:
                kb = {
                    "inline_keyboard": [
                        [{"text": "🔄 Продлить подписку", "callback_data": "buy"}]
                    ]
                }
                await bot.send_message(
                    sub.user_id,
                    "⏳ Ваша підписка закінчилась сьогодні. "
                    "Ви можете продовжити її ще на 30 днів.",
                    reply_markup=kb,
                )
            except Exception:
                pass
            await update_subscription(
                sub.user_id, last_reminded_on=today, status="grace", updated_at=moment
            )

        # --- Переводим в expired после grace ---
        if (
            sub.status in {"active", "grace"}
            and grace_until
            and moment > grace_until
        ):
            try:
                # удаляем из канала
                await bot.ban_chat_member(settings.CHANNEL_ID, sub.user_id)
                await bot.unban_chat_member(settings.CHANNEL_ID, sub.user_id)
            except Exception:
                pass
            await update_subscription(sub.user_id, status="expired", updated_at=moment)

    # --- Чистка канала от «левых» пользователей ---
    try:
        members = await bot.get_chat_administrators(settings.CHANNEL_ID)
        admins = {m.user.id for m in members}

        for sub in subs:
            if sub.user_id in admins:
                continue  # админов не трогаем
            if not await has_active_access(sub.user_id):
                try:
                    await bot.ban_chat_member(settings.CHANNEL_ID, sub.user_id)
                    await bot.unban_chat_member(settings.CHANNEL_ID, sub.user_id)
                except Exception:
                    pass
    except Exception:
        # если бот не может получить список участников (ограничение API) — пропускаем
        pass


