import logging
from bot.payments import create_invoice
import asyncio

log = logging.getLogger("bot.test")
async def run():
    url = await create_invoice(user_id=12345, amount=199)
    log.info("Invoice URL: %s", url)

asyncio.run(run())
