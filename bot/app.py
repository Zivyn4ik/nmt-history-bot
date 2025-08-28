from __future__ import annotations

import logging
import asyncio
from datetime import datetime, timedelta
from urllib.parse import urlparse
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from starlette.responses import JSONResponse

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import Update, Message
from aiogram.exceptions import TelegramRetryAfter

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import select

from bot.db import Session, PaymentToken, init_db
from bot.config import settings
from bot.handlers_start import router as start_router
from bot.handlers import router as handlers_router
from bot.handlers_wipe import router as wipe_router
from bot.handlers_buy import router as buy_router
from bot.services import enforce_expirations
from bot.payments.wayforpay import process_callback

log = logging.getLogger("app")

bot = Bot(token=settings.BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
BOT_USERNAME: str | None = None
dp = Dispatcher()

dp.include_router(start_router)
dp.include_router(handlers_router)
dp.include_router(wipe_router)
dp.include_router(buy_router)


@asynccontextmanager
async def lifespan(app: FastAPI):
    global BOT_USERNAME

    await init_db()

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

    try:
        me = await bot.get_me()
        BOT_USERNAME = me.username
        log.info("🤖 BOT_USERNAME установлен: @%s", BOT_USERNAME)
    except Exception as e:
        log.exception("Не удалось получить username бота: %s", e)

    scheduler = AsyncIOScheduler(timezone="UTC")
    scheduler.add_job(enforce_expirations, CronTrigger(hour=9, minute=0), kwargs={"bot": bot})
    scheduler.add_job(enforce_expirations, CronTrigger(hour="*/6"), kwargs={"bot": bot})
    scheduler.start()
    log.info("Scheduler запущен: подписки проверяются ежедневно и каждые 6 часов")

    yield
    await bot.session.close()


app = FastAPI(title="TG Subscription Bot", lifespan=lifespan)


def normalize_base_url(u: str) -> str:
    u = (u or "").strip()
    if not urlparse(u).scheme:
        u = "https://" + u
    return u.rstrip("/")


@app.get("/")
async def root():
    return {"ok": True}


@app.get("/healthz")
async def healthz():
    return {"ok": True}


@app.api_route("/thanks", methods=["GET", "POST", "HEAD"])
async def thanks_page(request: Request):
    return HTMLResponse("""
    <html>
    <head><title>Дякуємо за оплату!</title></head>
    <body>
        <h2>✅ Оплата пройшла успішно!</h2>
        <p>Бот щойно надіслав вам особисте посилання в Telegram 📩</p>
        <p>Відкрийте чат з ботом, щоб увійти до каналу.</p>
    </body>
    </html>
    """)


@app.api_route("/wfp/return", methods=["GET", "POST", "HEAD"])
async def wfp_return(request: Request):
    token_param = request.query_params.get("token")
    try:
        data = await request.json()
        token_param = token_param or data.get("token")
    except Exception:
        data = {}

    if not token_param:
        return HTMLResponse("<h2>❌ Не передан token</h2>", status_code=400)

    async with Session() as s:
        res = await s.execute(
            select(PaymentToken)
            .where(PaymentToken.token == token_param)
            .order_by(PaymentToken.created_at.desc())
        )
        token_obj = res.scalar_one_or_none()

        if not token_obj:
            return HTMLResponse("<h2>❌ Токен не найден</h2>", status_code=404)

        if token_obj.created_at < datetime.utcnow() - timedelta(hours=24):
            return HTMLResponse("<h2>❌ Токен устарел</h2>", status_code=400)

        if token_obj.used:
            return HTMLResponse("<h2>❌ Токен уже использован</h2>", status_code=400)

        if not BOT_USERNAME:
            return HTMLResponse("<h2>⚠️ BOT_USERNAME не установлен</h2>", status_code=500)

        # Отправляем пользователя в бот для отслеживания статуса
        invite_url = f"https://t.me/{BOT_USERNAME}?start={token_obj.token}"
        return RedirectResponse(invite_url)


# Проверка статуса оплаты через бота
async def wait_for_payment(user_id: int, token: str, timeout: int = 30):
    async with Session() as s:
        start_time = datetime.utcnow()
        message: Message = await bot.send_message(user_id, "⏳ Генерируем приглашение…")
        while (datetime.utcnow() - start_time).total_seconds() < timeout:
            res = await s.execute(
                select(PaymentToken)
                .where(PaymentToken.token == token)
            )
            token_obj = res.scalar_one_or_none()
            if token_obj and token_obj.status == "paid":
                token_obj.used = True
                await s.commit()
                await message.delete()
                invite_url = f"https://t.me/{BOT_USERNAME}?start={token}"
                await bot.send_message(user_id, f"✅ Оплата подтверждена! Вот ваша ссылка: {invite_url}")
                return True
            await asyncio.sleep(1)
        await message.edit_text("❌ Оплата не подтвердилась за 30 секунд. Попробуйте позже.")
        return False


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

