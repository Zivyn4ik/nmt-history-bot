import os
from pydantic import BaseSettings

class Settings(BaseSettings):
    BASE_URL: str = os.getenv("BASE_URL", "http://localhost:8000")
    BOT_TOKEN: str = os.getenv("BOT_TOKEN", "")
    CHANNEL_ID: int = int(os.getenv("CHANNEL_ID", "0"))
    CURRENCY: str = os.getenv("CURRENCY", "UAH")
    DATABASE_URL: str = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///./bot.sqlite3")
    PRICE: int = int(os.getenv("PRICE", "1"))
    PRODUCT_NAME: str = os.getenv("PRODUCT_NAME", "Access to course (1 month)")
    WFP_DOMAIN: str = os.getenv("WFP_DOMAIN", "")
    WFP_MERCHANT: str = os.getenv("WFP_MERCHANT", "")
    WFP_SECRET: str = os.getenv("WFP_SECRET", "")

    @property
    def return_url(self) -> str:
        return f"{self.BASE_URL}/wfp/return"

    @property
    def service_url(self) -> str:
        return "https://api.wayforpay.com/api"

settings = Settings()

