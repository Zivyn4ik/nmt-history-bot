# bot/handlers_smoke.py
from __future__ import annotations

import logging
from aiogram import Router
from aiogram.types import CallbackQuery

log = logging.getLogger("handlers_smoke")
router = Router()

@router.callback_query()
async def smoke(cb: CallbackQuery):
    try:
        await cb.answer("✅ smoke ok", cache_time=0, show_alert=False)
    except Exception:
        pass
    try:
        await cb.message.answer(f"🔎 smoke: data={cb.data!r}")
    except Exception:
        pass
    try:
        log.info("smoke handled: data=%r from=%s", cb.data, cb.from_user.id)
    except Exception:
        pass
