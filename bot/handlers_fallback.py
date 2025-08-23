# bot/handlers_fallback.py
from __future__ import annotations

import logging
from aiogram import Router
from aiogram.types import CallbackQuery

log = logging.getLogger("handlers_fallback")
router = Router()

@router.callback_query()
async def fallback_answer(cb: CallbackQuery):
    # мгновенно снимаем «часики» на ЛЮБОЙ кнопке
    try:
        await cb.answer(cache_time=1, show_alert=False)
    except Exception:
        pass
    try:
        log.info("fallback answered: data=%r from=%s", cb.data, cb.from_user.id)
    except Exception:
        pass
