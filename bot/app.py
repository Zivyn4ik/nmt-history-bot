from __future__ import annotations

import logging
from urllib.parse import urlparse

from fastapi import FastAPI, Request
from starlette.responses import JSONResponse, HTMLResponse, RedirectResponse

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import Update

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from .config import settings
from .db import init_db
from .handlers import router as handlers_router
from .handlers_wipe import router as wipe_router
from .handlers_buy import router as buy_router
from .services import enforce_expirations
from .payments.wayforpay import process_callback

log = logging.getLogger("app")

# ---------------- Aiogram ----------------
bot = Bot(
    token=settings.BOT_TOKEN,
    default=DefaultBotProperties(parse_mode=ParseMode.HTML),
)
dp = Dispatcher()
dp.include_router(handlers_router)  # /start + автоапрув join-request
dp.include_router(wipe_router)      # /wipe_me
dp.include_router(buy_router)       # /buy

# ---------------- FastAPI ----------------
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

@app.api_route("/thanks", methods=["GET", "POST", "HEAD"])
async def thanks_page():
    return HTMLResponse("""
    <html>
    <head>
        <title>Дякуємо за оплату!</title>
        <meta http-equiv="refresh" content="2;url=https://t.me/+kgfXNg9m0Sw5N2Uy">
        <style>
            body { background-color: #111; color: #eee; font-family: sans-serif; text-align: center; padding-top: 100px; }
            a { color: #4cc9f0; font-size: 18px; }
        </style>
    </head>
    <body>
        <h2>✅ Оплата пройшла успішно!</h2>
        <p>Через 2 секунди вас буде автоматично перенаправлено у Telegram-канал.</p>
        <p>Якщо цього не сталося, натисніть <a href="https://t.me/+x6gkdU02VdM2YzUy">сюди</a>.</p>
    </body>
    </html>
    """)


# ✅ Заменённый wfp_return с HTML и редиректом
@app.api_route("/wfp/return", methods=["GET", "POST", "HEAD"])
async def wfp_return():
    return HTMLResponse("""
    <html>
    <head>
        <title>Оплата успішна ✅</title>
        <meta http-equiv="refresh" content="1;url=https://t.me/+your_channel_invite">
    </head>
    <body style="background-color: #111; color: #eee; text-align: center; padding-top: 100px;">
        <h2>✅ Оплата пройшла успішно</h2>
        <p>Зачекайте або <a href="https://t.me/+your_channel_invite" style="color: #4cc9f0;">перейдіть у Telegram канал вручну</a>.</p>
    </body>
    </html>
    """)

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

    try:
        base = normalize_base_url(settings.BASE_URL)
        webhook_url = f"{base}/telegram/webhook"
        await bot.set_webhook(webhook_url)
        log.info("Telegram webhook set to %s", webhook_url)
    except Exception as e:
        log.exception("Failed to set webhook: %s", e)

    scheduler = AsyncIOScheduler(timezone="UTC")
    scheduler.add_job(
        enforce_expirations,
        CronTrigger(hour=9, minute=0),
        kwargs={"bot": bot},
    )
    scheduler.start()
    log.info("Scheduler started")

@app.on_event("shutdown")
async def on_shutdown():
    await bot.session.close()
