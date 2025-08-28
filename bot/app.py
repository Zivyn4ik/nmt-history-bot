from __future__ import annotations
import logging
import asyncio
from urllib.parse import urlparse
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from starlette.responses import JSONResponse
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import Update
from aiogram.exceptions import TelegramRetryAfter
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import select
from bot.db import Session, Payment, PaymentToken, init_db
from bot.handlers_start import router as start_router
from bot.handlers import router as handlers_router
from bot.handlers_wipe import router as wipe_router
from bot.handlers_buy import router as buy_router
from bot.services import enforce_expirations
from bot.payments.wayforpay import create_invoice, validate_wfp_signature

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
            except TelegramRetryAfter as e:
                await asyncio.sleep(e.retry_after)
                await bot.set_webhook(webhook_url)
    except Exception as e:
        log.exception("Ошибка webhook: %s", e)
    try:
        me = await bot.get_me()
        BOT_USERNAME = me.username
    except Exception as e:
        log.exception("Не удалось получить username бота: %s", e)
    scheduler = AsyncIOScheduler(timezone="UTC")
    scheduler.add_job(enforce_expirations, CronTrigger(hour=9, minute=0), kwargs={"bot": bot})
    scheduler.add_job(enforce_expirations, CronTrigger(hour="*/6"), kwargs={"bot": bot})
    scheduler.start()
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
    order_ref = (request.query_params.get("orderReference") or request.query_params.get("orderRef"))
    if not order_ref:
        try:
            data = await request.json()
            order_ref = data.get("orderReference") or data.get("orderRef")
        except Exception:
            data = {}
    if not order_ref:
        return HTMLResponse("<h2>❌ Не передан orderReference</h2>", status_code=400)
    async with Session() as s:
        res = await s.execute(select(Payment).where(Payment.order_ref == order_ref))
        pay = res.scalar_one_or_none()
        if not pay:
            return HTMLResponse("<h2>❌ Платеж не найден</h2>", status_code=404)
        res = await s.execute(
            select(PaymentToken).where(PaymentToken.user_id == pay.user_id, PaymentToken.status == "pending").order_by(PaymentToken.created_at.desc())
        )
        token_obj = res.scalar_one_or_none()
        if not token_obj:
            return HTMLResponse("<h2>❌ Токен уже использован или не найден</h2>", status_code=404)
        token_obj.status = "paid"
        await s.commit()
        if not BOT_USERNAME:
            return HTMLResponse("<h2>⚠️ BOT_USERNAME не установлен</h2>", status_code=500)
        invite_url = f"https://t.me/{BOT_USERNAME}?start={token_obj.token}"
        return RedirectResponse(invite_url)

@app.post("/telegram/webhook")
async def telegram_webhook(request: Request):
    data = await request.json()
    update = Update.model_validate(data)
    await dp.feed_update(bot, update)
    return JSONResponse({"ok": True})

@app.post("/wfp/return")
async def wfp_return(request: Request):
    try:
        data = await request.json()
    except Exception:
        data = {}
    order_ref = request.query_params.get("orderReference") or request.query_params.get("orderRef") or data.get("orderReference") or data.get("orderRef")
    if not order_ref:
        return HTMLResponse("<h2>❌ Не передан orderReference</h2>", status_code=400)
    async with Session() as s:
        res = await s.execute(select(Payment).where(Payment.order_ref == order_ref))
        pay = res.scalar_one_or_none()
        if not pay:
            return HTMLResponse("<h2>❌ Платеж не найден</h2>", status_code=404)
        res = await s.execute(
            select(PaymentToken).where(PaymentToken.user_id == pay.user_id, PaymentToken.status == "pending").order_by(PaymentToken.created_at.desc())
        )
        token_obj = res.scalar_one_or_none()
        if not token_obj:
            return HTMLResponse("<h2>❌ Токен уже использован или не найден</h2>", status_code=404)
        token_obj.status = "paid"
        await s.commit()
        if not BOT_USERNAME:
            return HTMLResponse("<h2>⚠️ BOT_USERNAME не установлен</h2>", status_code=500)
        invite_url = f"https://t.me/{BOT_USERNAME}?start={token_obj.token}"
        return RedirectResponse(invite_url)
