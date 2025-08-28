import os
from pydantic import BaseModel
from pydantic_settings import BaseSettings 
from pydantic import Field

class Settings(BaseModel):
    BASE_URL: str = os.getenv("BASE_URL", "http://localhost:8000")
    BOT_TOKEN: str = os.getenv("BOT_TOKEN", "")
    CHANNEL_ID: int = int(os.getenv("CHANNEL_ID", "0"))
    CURRENCY: str = os.getenv("CURRENCY", "UAH")
    DATABASE_URL: str = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///./bot.sqlite3")
    LANG: str = os.getenv("LANG", "ua")
    PRICE: int = int(os.getenv("PRICE", "1"))
    PRODUCT_NAME: str = os.getenv("PRODUCT_NAME", "Access to course (1 month)")
    TG_JOIN_REQUEST_URL: str = os.getenv("TG_JOIN_REQUEST_URL", "")
    WFP_DOMAIN: str = os.getenv("WFP_DOMAIN", "")
    WFP_MERCHANT: str = os.getenv("WFP_MERCHANT", "")
    WFP_SECRET: str = os.getenv("WFP_SECRET", "")

    # вычисляемые:
    @property
    def return_url(self) -> str:
        # страница, куда редиректит WayForPay после оплаты
        return f"{self.BASE_URL}/wfp/return"
    
    @property
    def service_url(self) -> str:
        # endpoint API WayForPay
        return "https://api.wayforpay.com/api"

settings = Settings()



