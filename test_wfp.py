import asyncio
from bot.config import settings
from bot.payments import create_invoice

async def test_wfp():
    url = await create_invoice(user_id=12345, amount=199)
    print("Invoice URL:", url)

asyncio.run(test_wfp())
