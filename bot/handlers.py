from __future__ import annotations
from aiogram import Router
from aiogram.filters import CommandStart, Command
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, ChatJoinRequest
from .services import ensure_user, get_subscription_status, has_active_access
from .payments.wayforpay import create_invoice
from .config import settings

router = Router()

@router.message(CommandStart())
async def cmd_start(m: Message):
    await ensure_user(m.from_user)
    text_ua = (
        "Вітаю! Я бот доступу до каналу.\n\n"
        "Команди:\n"
        "• /buy — оплатити/продовжити підписку\n"
        "• /status — перевірити статус\n"
        "Після оплати надішлю запрошення або схвалю запит на вступ."
    )
    text_ru = (
        "Привет! Я бот доступа к каналу.\n\n"
        "Команды:\n"
        "• /buy — оплатить/продлить подписку\n"
        "• /status — статус подписки\n"
        "После оплаты пришлю приглашение или одобрю запрос на вступление."
    )
    await m.answer(text_ua if settings.LANG == "ua" else text_ru)

@router.message(Command("status"))
async def cmd_status(m: Message):
    sub = await get_subscription_status(m.from_user.id)
    until = sub.paid_until.strftime("%Y-%m-%d") if sub.paid_until else "—"
    text_ua = f"Статус: {sub.status}. Оплачено до: {until}"
    text_ru = f"Статус: {sub.status}. Оплачено до: {until}"
    await m.answer(text_ua if settings.LANG == "ua" else text_ru)

@router.message(Command("buy"))
async def cmd_buy(m: Message):
    url = await create_invoice(m.from_user.id, amount=settings.PRICE, currency=settings.CURRENCY, product_name=settings.PRODUCT_NAME)
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=("Оплатити" if settings.LANG=='ua' else "Оплатить"), url=url)]])
    await m.answer(("Рахунок на 1 місяць сформовано. Натисніть «Оплатити».") if settings.LANG=='ua' else ("Счёт на 1 месяц сформирован. Нажмите «Оплатить»."), reply_markup=kb)

@router.chat_join_request()
async def on_join(event: ChatJoinRequest):
    if await has_active_access(event.from_user.id):
        await event.approve()
        await event.bot.send_message(event.from_user.id, "Доступ надано. Ласкаво просимо!" if settings.LANG=='ua' else "Доступ предоставлен. Добро пожаловать!")
    else:
        await event.bot.send_message(event.from_user.id, "У вас немає активної підписки. Натисніть /buy і після оплати знову подайте запит." if settings.LANG=='ua' else "У вас нет активной подписки. Нажмите /buy и после оплаты снова запросите доступ.")
