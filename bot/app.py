# bot/app.py
from __future__ import annotations

import logging
from fastapi import FastAPI, Request
from starlette.responses import HTMLResponse
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import Update
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from .config import settings
from .db import init_db
from .services import enforce_expirations
from .handlers_start import router as start_router
from .handlers import router as handlers_router
from .handlers_wipe import router as wipe_router
from .handlers_buy import router as buy_router
from .payments.wayforpay import process_callback

log = logging.getLogger("app")

app = FastAPI()

# Важно: здесь используем именно BOT_TOKEN (как в config.py)
bot = Bot(token=settings.BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()

# Подключаем aiogram-роутеры
dp.include_router(start_router)
dp.include_router(handlers_router)
dp.include_router(wipe_router)
dp.include_router(buy_router)

@app.get("/", response_class=HTMLResponse)
async def root():
    return "<b>NMT History Bot is live</b>"

# Вебхук от Telegram
@app.post("/telegram/webhook")
async def telegram_webhook(update: dict):
    upd = Update.model_validate(update)
    await dp.feed_update(bot, upd)
    return {"ok": True}

# Коллбек WayForPay — ОБЯЗАТЕЛЬНО передаём bot внутрь
@app.post("/payments/wayforpay/callback")
async def wfp_callback(request: Request):
    return await process_callback(request, bot)

# Планировщик для ежедневных операций
scheduler: AsyncIOScheduler | None = None

@app.on_event("startup")
async def on_startup():
    await init_db()
    global scheduler
    scheduler = AsyncIOScheduler()
    scheduler.add_job(enforce_expirations, CronTrigger(hour=9, minute=0), args=[bot])
    scheduler.start()
    log.info("App started, scheduler running")

@app.on_event("shutdown")
async def on_shutdown():
    global scheduler
    if scheduler:
        scheduler.shutdown(wait=False)
    log.info("App stopped")
