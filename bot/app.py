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
from .handlers import router as handlers_router
from .services import enforce_expirations
from .payments.wayforpay import process_callback

log = logging.getLogger("app")

# --- Aiogram bot/dispatcher ---
bot = Bot(token=settings.BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()
dp.include_router(handlers_router)

# --- FastAPI app ---
app = FastAPI(title="TG Subscription Bot")


# ---------- helpers ----------
def normalize_base_url(u: str) -> str:
    """Добавляет https:// при необходимости и убирает хвостовой слэш."""
    u = (u or "").strip()
    if not urlparse(u).scheme:
        u = "https://" + u
    return u.rstrip("/")


# ---------- routes ----------
@app.get("/")
async def root():
    return {"ok": True}

@app.get("/healthz")
async def healthz():
    return {"ok": True}

@app.get("/thanks")
async def thanks_page():
    return HTMLResponse("<h3>Дякуємо за оплату! Можете повернутися до бота.</h3>")

@app.post("/telegram/webhook")
async def telegram_webhook(request: Request):
    data = await request.json()
    update = Update.model_validate(data)
    await dp.feed_update(bot, update)
    return JSONResponse({"ok": True})

@app.post("/payments/wayforpay/callback")
async def wayforpay_callback(req: Request):
    try:
        data = await req.json()
    except Exception:
        data = {}
    await process_callback(bot, data)
    return {"ok": True}


# ---------- lifecycle ----------
@app.on_event("startup")
async def on_startup():
    await init_db()

    # Устанавливаем вебхук
    try:
        base = normalize_base_url(settings.BASE_URL)
        webhook_url = f"{base}/telegram/webhook"
        await bot.set_webhook(webhook_url)
        log.info("Telegram webhook set to %s", webhook_url)
    except Exception as e:
        log.exception("Failed to set webhook: %s", e)

    # Планировщик ежедневных задач
    scheduler = AsyncIOScheduler(timezone="UTC")
    scheduler.add_job(enforce_expirations, CronTrigger(hour=9, minute=0), kwargs={"bot": bot})
    scheduler.start()
    log.info("Scheduler started")

@app.on_event("shutdown")
async def on_shutdown():
    await bot.session.close()
