from aiogram import Router, F, Bot
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton
import asyncio
from bot.db import async_session_maker, get_or_create_user, User
from bot.services import activate_subscription, remaining_days
from bot.payments.wayforpay import create_invoice, check_status

router = Router(name="buy")

def pay_kb(url: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="💳 Оплатить", url=url)]]
    )

@router.message(F.text == "Оформить подписку")
async def handle_buy(message: Message):
    async with async_session_maker() as session:
        user = await get_or_create_user(session, message.from_user.id)
        order_ref, invoice_url = await create_invoice(user.id)
        user.order_reference = order_ref
        await session.commit()
        await message.answer("💳 Нажмите кнопку ниже, чтобы оплатить подписку.", reply_markup=pay_kb(invoice_url))
        await message.answer("Оплатили? Натисніть «Проверить подписку» або поверніться з Return-сторінки у бота.")

@router.message(F.text == "Проверить подписку")
async def handle_check_afterpay(message: Message):
    async with async_session_maker() as session:
        user: User = await get_or_create_user(session, message.from_user.id)
        if not user.order_reference:
            if user.status == "ACTIVE":
                days = remaining_days(user)
                end = user.end_date.strftime("%d.%m.%Y") if user.end_date else "—"
                await message.answer(f"✅ У вас активная подписка.\nДата окончания: {end} (осталось {days} дн.)")
            else:
                await message.answer("❌ У вас нет активной подписки.\nОформите её, чтобы получить доступ к каналу.")
            return

        await message.answer("⏳ Проверяем оплату, генерируем доступ…")
        for _ in range(35):
            data = await check_status(user.order_reference)
            status = data.get("transactionStatus") or data.get("orderStatus")
            if status and status.lower() == "approved":
                invite = await activate_subscription(message.bot, session, user)
                user.order_reference = None
                await session.commit()
                await message.answer(f"✅ Подписка активна! Вот ваша ссылка: {invite}")
                return
            await asyncio.sleep(1)
        await message.answer("❌ Оплата не подтверждена. Попробуйте позже или обратитесь в поддержку.")

@router.message(F.text == "Помощь")
async def handle_help(message: Message):
    await message.answer(
        "ℹ️ Если возникли вопросы:\n"
        "• Оплатите подписку и вернитесь в бота.\n"
        "• Нажмите «Проверить подписку», чтобы получить доступ.\n"
        "• Поддержка: напишите в чат бота."
    )
