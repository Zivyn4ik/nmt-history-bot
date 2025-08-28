from __future__ import annotations

import logging
from urllib.parse import urlparse
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from starlette.responses import JSONResponse, HTMLResponse

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import Update

from apscheduler.schedulers.asyncio import AsyncIOScheduler

import sys

bot: Optional[Bot] = None
dp: Optional[Dispatcher] = None
BOT_USERNAME: Optional[str] = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    global bot, dp, BOT_USERNAME
    await init_db()

    bot = Bot(token=settings.BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    me = await bot.get_me()
    BOT_USERNAME = me.username

    dp = Dispatcher()
    dp.include_router(root_router)

    # стартуем long-polling как фоновую таску
    loop = app.router.lifespan_context.__self__.state.loop  # доступ к event loop FastAPI/uvicorn
    loop.create_task(dp.start_polling(bot))

    yield

    # shutdown
    await bot.session.close()

app = FastAPI(lifespan=lifespan)

@app.get("/", response_class=PlainTextResponse)
async def root():
    return "OK"

@app.get("/wfp/return", response_class=HTMLResponse)
async def wfp_return(request: Request):
    # простая страница после оплаты + кнопка вернуться в бота
    # deeplink без параметров — юзер нажмёт «Проверить подписку»
    # если хочешь — можно добавить параметр ?start=paid и обрабатывать отдельно
    bot_link = f"https://t.me/{BOT_USERNAME}"
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

