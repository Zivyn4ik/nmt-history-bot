from __future__ import annotations

from aiogram import Router, F, Bot

from aiogram.types import CallbackQuery

from .handlers_start import router as start_router
from .handlers_buy import router as buy_router
from .handlers_wipe import router as wipe_router

from bot.config import settings
from bot.services import get_subscription_status, is_member_of_channel, ensure_user
from bot.handlers_buy import cmd_buy 

router = Router(name="root")

router.include_router(start_router)
router.include_router(buy_router)
router.include_router(wipe_router)

