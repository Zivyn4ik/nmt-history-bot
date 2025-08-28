from aiogram import Router

from .handlers_start import router as start_router
from .handlers_buy import router as buy_router
from .handlers_wipe import router as wipe_router

router = Router(name="root")
router.include_router(start_router)
router.include_router(buy_router)
router.include_router(wipe_router)
