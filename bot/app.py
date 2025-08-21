from __future__ import annotations
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

bot = Bot(token=settings.BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()
dp.include_router(handlers_router)

app = FastAPI(title="TG Subscription Bot")

@app.on_event("startup")
async def startup():
    await init_db()
    await bot.set_webhook(f"{settings.BASE_URL}/telegram/webhook")
    sched = AsyncIOScheduler(timezone="UTC")
    sched.add_job(enforce_expirations, CronTrigger(minute="0"), args=[bot])
    sched.start()
    app.state.sched = sched

@app.on_event("shutdown")
async def shutdown():
    await bot.delete_webhook()
    sched = getattr(app.state, 'sched', None)
    if sched:
        sched.shutdown(wait=False)

@app.post("/telegram/webhook")
async def telegram_webhook(update: dict):
    await dp.feed_update(bot, Update.model_validate(update))
    return JSONResponse({"ok": True})

@app.post("/thanks")
async def thanks():
    return HTMLResponse("<h3>Дякуємо! Якщо оплата пройшла, бот надішле запрошення автоматично.</h3>")

@app.post("/payments/wayforpay/callback")
async def wayforpay_callback(req: Request):
    data = await req.json()
    await process_callback(bot, data)
    return {"ok": True}
