import asyncio
from datetime import datetime
from aiogram import Router, F
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import Command, CommandObject

from db import async_session_maker, get_or_create_user, User
from payments.wayforpay import create_invoice, check_status
from services import activate_subscription, remaining_days
from config import settings

router = Router(name="buy")

def pay_kb(url: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="💳 Оплатить", url=url)],
        ]
    )

@router.message(F.text == "Оформить подписку")
async def handle_buy(message: Message):
    async with async_session_maker() as session:
        user = await get_or_create_user(session, message.from_user.id)

        # создаём инвойс
        try:
            order_ref, invoice_url = await create_invoice(user.id)
        except Exception as e:
            await message.answer(f"⚠️ Помилка створення рахунку: {e}")
            return

        # сохраняем order_reference (status=PENDING не храним отдельно — статусом управляет логика)
        user.order_reference = order_ref
        await session.commit()

        await message.answer(
            "💳 Нажмите кнопку ниже, чтобы оплатить подписку.",
            reply_markup=pay_kb(invoice_url)
        )
        # Подсказка: после оплаты вас перекинет на страницу — там будет кнопка вернуться в бота
        await message.answer("Оплатили? Натисніть «Проверить подписку» або поверніться з Return-сторінки у бота.")

@router.message(F.text == "Проверить подписку")
async def handle_check_afterpay(message: Message):
    async with async_session_maker() as session:
        user: User = await get_or_create_user(session, message.from_user.id)

        if not user.order_reference:
            # просто офлайн-проверка без последней оплаты
            if user.status == "ACTIVE":
                days = remaining_days(user)
                end = user.end_date.strftime("%d.%m.%Y") if user.end_date else "—"
                await message.answer(f"✅ У вас активная подписка.\nДата окончания: {end} (осталось {days} дн.)")
            else:
                await message.answer("❌ У вас нет активной подписки.\nОформите её, чтобы получить доступ к каналу.")
            return

        await message.answer("⏳ Проверяем оплату, генерируем доступ…")

        # polling 35 сек, шаг 1 сек
        for _ in range(35):
            data = await check_status(user.order_reference)
            # Успешный платёж в WFP имеет transactionStatus=Approved
            status = data.get("transactionStatus") or data.get("orderStatus")
            if status and status.lower() == "approved":
                # активируем подписку/продлеваем
                invite = await activate_subscription(message.bot, session, user)

                # Чистим order_reference (опционально, чтобы не путать повторы)
                user.order_reference = None
                await session.commit()

                await message.answer(f"✅ Подписка активна! Вот ваша ссылка: {invite}")
                return
            await asyncio.sleep(1)

        await message.answer("❌ Оплата не подтверждена. Попробуйте позже или обратитесь в поддержку.")

@router.message(F.text == "Помощь")
async def handle_help(message: Message):
    await message.answer(
        "ℹ️ Якщо виникли питання:\n"
        "• Оплатіть підписку та поверніться в бота з Return-сторінки.\n"
        "• Натисніть «Проверить подписку», щоб отримати доступ.\n"
        "• Підтримка: напишіть у чат бота."
    )
