from pydantic_settings import BaseSettings
from pydantic import Field


class Settings(BaseSettings):
    BOT_TOKEN: str = Field(...)
    CHANNEL_ID: int = Field(...)
    BASE_URL: str = Field(...)
    WFP_MERCHANT: str = Field(...)
    WFP_SECRET: str = Field(...)
    WFP_DOMAIN: str = Field(...)
    PRICE: float = Field(default=199.0)
    CURRENCY: str = Field(default="UAH")
    PRODUCT_NAME: str = Field(default="Channel subscription (1 month)")
    LANG: str = Field(default="ua")
    DATABASE_URL: str = Field(default="sqlite+aiosqlite:///./bot.sqlite3")
    PAYMENT_URL: str = Field(default="https://secure.wayforpay.com/payment/sd11e605b4ab0")

    TG_JOIN_REQUEST_URL: str = Field(...)

    class Config:
        env_file = ".env"


settings = Settings()
