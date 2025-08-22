# bot/handlers_fallback.py
from __future__ import annotations

import logging
from aiogram import Router
from aiogram.types import CallbackQuery

log = logging.getLogger("handlers_fallback")
router = Router()

@router.callback_query()
async def fallback_answer(cb: CallbackQuery):
    # 1) мгновенно снимаем «часики»
    try:
        await cb.answer(cache_time=1, show_alert=False)
    except Exception:
        pass

    # 2) Лёгкий лог — пригодится, если основной хендлер не отработал
    try:
        log.info("fallback answered: data=%r from_user=%s", cb.data, cb.from_user.id)
    except Exception:
        pass
