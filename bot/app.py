from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, PlainTextResponse
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from bot.config import settings
from bot.db import init_db
from bot.handlers_start import router as start_router
from bot.handlers_buy import router as buy_router

app = FastAPI()

bot: Bot = Bot(token=settings.BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp: Dispatcher = Dispatcher()
dp.include_router(start_router)
dp.include_router(buy_router)

@app.on_event("startup")
async def startup():
    await init_db()

@app.get("/", response_class=PlainTextResponse)
async def root():
    return "OK"

@app.get("/wfp/return", response_class=HTMLResponse)
async def wfp_return(request: Request):
    bot_link = f"https://t.me/{await bot.get_me().username}"
    html = f"""
    <html><body>
      <h1>✅ Оплата принята в обработку</h1>
      <p>Вернитесь в бота, чтобы получить доступ.</p>
      <p><a href="{bot_link}">Перейти в бота</a></p>
    </body></html>
    """
    return HTMLResponse(html)
