from fastapi import FastAPI
from fastapi.responses import HTMLResponse, PlainTextResponse
import asyncio
import logging
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from bot.config import settings
from bot.db import init_db, async_session_maker
from bot.services import check_subscriptions
from bot.handlers_start import router as start_router
from bot.handlers import router as handlers_router
from bot.handlers_wipe import router as wipe_router
from bot.handlers_buy import router as buy_router

log = logging.getLogger("app")
app = FastAPI()

bot = Bot(token=settings.BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()
dp.include_router(start_router)
dp.include_router(handlers_router)
dp.include_router(wipe_router)
dp.include_router(buy_router)


@app.on_event("startup")
async def startup():
    await init_db()
    log.info("База данных инициализирована")

    # Запуск APScheduler
    scheduler = AsyncIOScheduler()
    scheduler.add_job(lambda: asyncio.create_task(check_subscriptions(bot, async_session_maker)),
                      "interval", minutes=60)
    scheduler.start()
    log.info("APScheduler запущен")

    # Запуск Telegram long-polling
    asyncio.create_task(dp.start_polling(bot))
    log.info("Bot polling запущен")


@app.on_event("shutdown")
async def shutdown():
    await bot.session.close()
    log.info("Bot session закрыта")


@app.get("/", response_class=PlainTextResponse)
async def root():
    return "OK"


@app.get("/wfp/return", response_class=HTMLResponse)
async def wfp_return():
    bot_link = f"https://t.me/{(await bot.get_me()).username}"
    html = f"""
    <!doctype html>
    <html lang="uk">
      <head>
        <meta charset="utf-8" />
        <meta name="viewport" content="width=device-width,initial-scale=1" />
        <title>Оплата отримана</title>
        <style>
          body {{ font-family: system-ui, -apple-system, Segoe UI, Roboto, Arial, sans-serif; background:#0f172a; color:#e2e8f0; display:flex; align-items:center; justify-content:center; min-height:100vh; margin:0; }}
          .card {{ background:#111827; border:1px solid #1f2937; padding:32px; border-radius:16px; max-width:560px; text-align:center; box-shadow:0 10px 30px rgba(0,0,0,0.4); }}
          h1 {{ margin:0 0 8px; font-size:28px; }}
          p {{ margin:8px 0 18px; line-height:1.5; color:#cbd5e1; }}
          a.btn {{ display:inline-block; padding:12px 18px; border-radius:12px; background:#22c55e; color:#0b1220; text-decoration:none; font-weight:700; }}
        </style>
      </head>
      <body>
        <div class="card">
          <h1>✅ Оплата прийнята в обробку</h1>
          <p>Поверніться у бота, щоб отримати доступ.</p>
          <p><a class="btn" href="{bot_link}">Перейти в бота</a></p>
        </div>
      </body>
    </html>
    """
    return HTMLResponse(content=html, status_code=200)
