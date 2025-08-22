# bot/handlers.py
from __future__ import annotations

from aiogram import Router, F, Bot
from aiogram.types import ChatJoinRequest, Message
from aiogram.filters import Command

from .config import settings
from .services import ensure_user, has_active_access, get_subscription_status

router = Router()


@router.message(Command("status"))
async def cmd_status(message: Message, bot: Bot):
    """
    Запрос статуса подписки текстом (дополнительная команда по желанию).
    Никаких "активна до..." без подтвержденной оплаты!
    """
    await ensure_user(message.from_user)
    uid = message.from_user.id

    if await has_active_access(uid):
        sub = await get_subscription_status(uid)
        until_text = sub.paid_until.strftime("%Y-%m-%d") if sub and sub.paid_until else "невідомо"
        await message.answer(f"✅ Підписка активна до {until_text}.\nПосилання: {settings.TG_JOIN_REQUEST_URL}")
    else:
        # только кнопка покупки (никаких /buy текстом)
        from .handlers_buy import send_buy_button
        await message.answer("❌ Активної підписки немає.")
        await send_buy_button(message, bot)


@router.chat_join_request(F.chat.id == settings.CHANNEL_ID)
async def on_join_request(event: ChatJoinRequest, bot: Bot):
    """
    Автоапрув join-request только если доступ действительно активный.
    Никаких сообщений про активную подписку тут.
    """
    uid = event.from_user.id
    if await has_active_access(uid):
        try:
            await bot.approve_chat_join_request(chat_id=settings.CHANNEL_ID, user_id=uid)
        except Exception:
            pass
    else:
        # Молча отклоняем запрос и отправляем пользователю кнопку оплаты
        try:
            await bot.decline_chat_join_request(chat_id=settings.CHANNEL_ID, user_id=uid)
            from .handlers_buy import send_buy_button
            await bot.send_message(uid, "Щоб увійти до каналу — оформіть підписку.")
            # отдельным сообщением — кнопка
            dummy = Message(message_id=0, date=event.date, chat=event.chat)  # заглушка ради сигнатуры
            dummy.from_user = event.from_user  # type: ignore
            await send_buy_button(dummy, bot)
        except Exception:
            pass
