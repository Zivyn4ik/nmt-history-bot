from __future__ import annotations

import logging
import os
import asyncio
from urllib.parse import urlparse
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from starlette.responses import JSONResponse, HTMLResponse

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import Update
from aiogram.exceptions import TelegramRetryAfter

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import select

from bot.db import Session, Payment
from bot.config import settings
from bot.db import init_db
from bot.handlers_start import router as start_router
from bot.handlers import router as handlers_router
from bot.handlers_wipe import router as wipe_router
from bot.handlers_buy import router as buy_router
from bot.services import enforce_expirations
from bot.payments.wayforpay import process_callback

log = logging.getLogger("app")

# ---------------- Aiogram ----------------
bot = Bot(
    token=settings.BOT_TOKEN,
    default=DefaultBotProperties(parse_mode=ParseMode.HTML),
)
BOT_USERNAME: str | None = None   # 🔹 здесь сохраним username
dp = Dispatcher()

# порядок важен: сперва стартовое меню, затем прочие роутеры
dp.include_router(start_router)
dp.include_router(handlers_router)
dp.include_router(wipe_router)
dp.include_router(buy_router)


# ---------------- FastAPI ----------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    global BOT_USERNAME

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

    # 🔹 Получаем username бота (важно для ссылок с токенами)
    try:
        me = await bot.get_me()
        BOT_USERNAME = me.username
        log.info("🤖 BOT_USERNAME установлен: @%s", BOT_USERNAME)
    except Exception as e:
        log.exception("Не удалось получить username бота: %s", e)

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
        <style>
            body {{ background-color: #111; color: #eee; font-family: sans-serif; text-align: center; padding-top: 100px; }}
            a {{ color: #4cc9f0; font-size: 18px; }}
        </style>
    </head>
    <body>
        <h2>✅ Оплата пройшла успішно!</h2>
        <p>Бот щойно надіслав вам особисте посилання в Telegram 📩</p>
        <p>Відкрийте чат з ботом, щоб увійти до каналу.</p>
    </body>
    </html>
    """)

@app.api_route("/wfp/return", methods=["GET", "POST", "HEAD"])
async def wfp_return(request: Request):
    """
    WayForPay редиректит пользователя сюда после оплаты.
    Поддерживаем два сценария:
      1) returnUrl был с ?token=<token> -> в запросе будет token
         — тогда просто помечаем токен как paid и редиректим в t.me/<bot>?start=<token>
      2) если пришёл orderReference — ищем Payment по order_ref,
         берём последний pending token для pay.user_id и помечаем paid,
         далее редиректим в t.me/<bot>?start=<token>
    """
    from bot.db import PaymentToken  # локально, чтобы избежать циклов импорта

    # 1) token-first flow (create_invoice добавляет ?token=...)
    token = request.query_params.get("token")
    if token:
        async with Session() as s:
            res = await s.execute(select(PaymentToken).where(PaymentToken.token == token))
            token_obj = res.scalar_one_or_none()
            if not token_obj:
                return HTMLResponse("<h2>❌ Токен не знайдено</h2>", status_code=404)

            # помечаем paid (если ещё pending)
            if token_obj.status == "pending":
                token_obj.status = "paid"
                await s.commit()

        if not BOT_USERNAME:
            return HTMLResponse("<h2>⚠️ BOT_USERNAME не встановлено</h2>", status_code=500)
        return RedirectResponse(f"https://t.me/{BOT_USERNAME}?start={token}")

    # 2) orderReference flow
    order_ref = request.query_params.get("orderReference") or request.query_params.get("orderReference[]")
    if not order_ref:
        return HTMLResponse("<h2>❌ Не передано orderReference</h2>", status_code=400)

    async with Session() as s:
        # ищем Payment
        res = await s.execute(select(Payment).where(Payment.order_ref == order_ref))
        pay = res.scalar_one_or_none()
        if not pay:
            return HTMLResponse("<h2>❌ Платеж не знайдено</h2>", status_code=404)

        # ищем последний pending token для этого user_id
        res = await s.execute(
            select(PaymentToken)
            .where(PaymentToken.user_id == pay.user_id)
            .order_by(PaymentToken.created_at.desc())
        )
        token_obj = res.scalar_one_or_none()
        if not token_obj:
            return HTMLResponse("<h2>❌ Токен не знайдено для користувача</h2>", status_code=404)

        if token_obj.status == "pending":
            token_obj.status = "paid"
            await s.commit()

    if not BOT_USERNAME:
        return HTMLResponse("<h2>⚠️ BOT_USERNAME не встановлено</h2>", status_code=500)

    return RedirectResponse(f"https://t.me/{BOT_USERNAME}?start={token_obj.token}")


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
