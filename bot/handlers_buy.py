# bot/handlers_buy.py
from __future__ import annotations

import logging
from aiogram import Router, F, Bot
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton

from .services import ensure_user
from .payments.wayforpay import create_invoice
from .config import settings

log = logging.getLogger("handlers_buy")
router = Router()

def _pay_kb(url: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Оплатити", url=url)]
    ])

@router.message(F.text == "/buy")
async def cmd_buy(message: Message, bot: Bot):
    user_id = message.from_user.id
    await ensure_user(message.from_user)

    url = await create_invoice(
        user_id=user_id,
        amount=settings.PRICE,
        currency=settings.CURRENCY,
        product_name=settings.PRODUCT_NAME,
    )
    await message.answer(
        "Рахунок на 1 місяць сформовано. Натисніть «Оплатити».",
        reply_markup=_pay_kb(url)
    )

@router.callback_query(F.data == "buy_open")
async def on_buy_open(cb: CallbackQuery, bot: Bot):
    user_id = cb.from_user.id
    log.info("buy_open click from %s", user_id)
    # Мгновенно убираем «часики»
    try:
        await cb.answer(cache_time=1, show_alert=False)
    except Exception:
        pass

    await ensure_user(cb.from_user)

    try:
        url = await create_invoice(
            user_id=user_id,
            amount=settings.PRICE,
            currency=settings.CURRENCY,
            product_name=settings.PRODUCT_NAME,
        )
        await cb.message.answer(
            "Рахунок на 1 місяць сформовано. Натисніть «Оплатити».",
            reply_markup=_pay_kb(url)
        )
    except Exception as e:
        log.exception("buy_open failed for %s: %s", user_id, e)
        try:
            await cb.message.answer("⚠️ Не вдалося сформувати рахунок. Спробуйте ще раз пізніше.")
        except Exception:
            pass
