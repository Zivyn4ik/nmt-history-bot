# bot/app.py
from __future__ import annotations

import logging
from urllib.parse import urlparse

from fastapi import FastAPI, Request
from starlette.responses import JSONResponse, HTMLResponse

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import Update

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from .config import settings
from .db import init_db
from .handlers_start import router as start_router
from .handlers import router as handlers_router
from .handlers_wipe import router as wipe_router
from .handlers_buy import router as buy_router
from .services import enforce_expirations
from .payments.wayforpay import process_callback

log = logging.getLogger("app")

# ---------- Aiogram ----------
bot = Bot(settings.BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()
dp.include_routers(start_router, buy_router, handlers_router, wipe_router)

# ---------- FastAPI ----------
app = FastAPI()


@app.on_event("startup")
async def on_startup():
    await init_db()

    # проверим URL чисто информативно
    try:
        urlparse(settings.BASE_URL)
    except Exception:
        log.warning("BASE_URL is invalid: %s", settings.BASE_URL)

    # ежедневная проверка подписок
    scheduler = AsyncIOScheduler(timezone="UTC")
    scheduler.add_job(enforce_expirations, CronTrigger(hour=6, minute=0), kwargs={"bot": bot})
    scheduler.start()
    log.info("Scheduler started")


# ----- Telegram webhook handlers (оба пути валидны) -----
async def _handle_telegram_webhook(update: dict):
    upd = Update.model_validate(update)
    await dp.feed_update(bot, upd)
    return JSONResponse({"ok": True})

@app.post("/webhook")
async def telegram_webhook_1(update: dict):
    return await _handle_telegram_webhook(update)

@app.post("/telegram/webhook")
async def telegram_webhook_2(update: dict):
    return await _handle_telegram_webhook(update)


# ----- WayForPay callback -----
@app.post("/wfp/callback")
async def wfp_callback(request: Request):
    res = await process_callback(request, bot)
    return JSONResponse(res)


# ----- Health / index -----
@app.get("/")
async def index():
    return HTMLResponse("<h3>Bot is running</h3>")


@app.on_event("shutdown")
async def on_shutdown():
    await bot.session.close()
