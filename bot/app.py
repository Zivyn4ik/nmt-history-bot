
from __future__ import annotations

import logging
import os
import asyncio
from urllib.parse import urlparse
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from starlette.responses import JSONResponse, HTMLResponse

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import Update
from aiogram.exceptions import TelegramRetryAfter

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from bot.config import settings
from bot.db import init_db
from bot.handlers_start import router as start_router
from bot.handlers import router as handlers_router
from bot.handlers_wipe import router as wipe_router
from bot.handlers_buy import router as buy_router
from bot.services import enforce_expirations
from bot.payments.wayforpay import process_callback

log = logging.getLogger("app")

# --------- общие настройки ---------
INVITE_URL = getattr(settings, "TG_INVITE_URL", "https://t.me/+kgfXNg9m0Sw5N2Uy")

# ---------------- Aiogram ----------------
bot = Bot(
    token=settings.BOT_TOKEN,
    default=DefaultBotProperties(parse_mode=ParseMode.HTML),
)
dp = Dispatcher()

# порядок важен: сперва стартовое меню, затем прочие роутеры
dp.include_router(start_router)
dp.include_router(handlers_router)
dp.include_router(wipe_router)
dp.include_router(buy_router)


# ---------------- FastAPI ----------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    # --- startup ---
    await init_db()

    # Безопасная установка webhook
    try:
        base = normalize_base_url(settings.BASE_URL)
        webhook_url = f"{base}/telegram/webhook"

        info = await bot.get_webhook_info()
        if info.url != webhook_url:
            try:
                await bot.set_webhook(webhook_url)
                log.info("Telegram webhook установлен: %s", webhook_url)
            except TelegramRetryAfter as e:
                log.warning("Flood control, retry через %s секунд", e.retry_after)
                await asyncio.sleep(e.retry_after)
                await bot.set_webhook(webhook_url)
        else:
            log.info("Webhook уже установлен, ничего не делаем")
    except Exception as e:
        log.exception("Ошибка при установке webhook: %s", e)

    # Scheduler
    scheduler = AsyncIOScheduler(timezone="UTC")
    scheduler.add_job(enforce_expirations, CronTrigger(hour=9, minute=0), kwargs={"bot": bot})
    scheduler.add_job(enforce_expirations, CronTrigger(hour="*/6"), kwargs={"bot": bot})
    scheduler.start()
    log.info("Scheduler запущен: подписки проверяются ежедневно и каждые 6 часов")

    # --- приложение работает ---
    yield

    # --- shutdown ---
    await bot.session.close()


app = FastAPI(title="TG Subscription Bot", lifespan=lifespan)


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
    return HTMLResponse(f"""
    <html>
    <head>
        <title>Дякуємо за оплату!</title>
        <p>✅ Оплата пройшла успішно!</p>
        <style>
            body {{ background-color: #111; color: #eee; font-family: sans-serif; text-align: center; padding-top: 100px; }}
            a {{ color: #4cc9f0; font-size: 18px; }}
        </style>
    </head>
    <body>
        <p>Бот щойно надіслав вам особисте посилання в Telegram 📩</p>
        <p>Через 2 секунди вас буде автоматично перенаправлено у Telegram-канал.</p>
        <p>Відкрийте чат з ботом, щоб увійти до каналу.</p>
    </body>
    </html>
    """)


@app.api_route("/wfp/return", methods=["GET", "POST", "HEAD"])
async def wfp_return():
    return HTMLResponse(f"""
    <html>
    <head>
        <title>Оплата успішна ✅</title>
        <meta http-equiv="refresh" content="1;url={INVITE_URL}">
        <style>
            body {{ background-color: #111; color: #eee; font-family: sans-serif; text-align: center; padding-top: 100px; }}
            a {{ color: #4cc9f0; font-size: 18px; }}
        </style>
    </head>
    <body>
        <h2>✅ Оплата пройшла успішно</h2>
        <p>Зачекайте або <a href="{INVITE_URL}">перейдіть у Telegram канал вручну</a>.</p>
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

    

