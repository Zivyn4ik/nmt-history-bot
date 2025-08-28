from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker, declarative_base
from sqlalchemy import Column, Integer, String, DateTime, ForeignKey
from datetime import datetime
from bot.config import settings

engine = create_async_engine(settings.DATABASE_URL, echo=True)
Session = sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)

Base = declarative_base()

class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, unique=True, index=True)
    balance = Column(Integer, default=0)

class Subscription(Base):
    __tablename__ = "subscriptions"
    user_id = Column(Integer, ForeignKey("users.id"), primary_key=True)
    status = Column(String, default="expired")
    paid_until = Column(DateTime, nullable=True)
    grace_until = Column(DateTime, nullable=True)
    updated_at = Column(DateTime, default=datetime.utcnow)

class Payment(Base):
    __tablename__ = "payments"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"))
    order_ref = Column(String, unique=True)
    amount = Column(Integer)
    currency = Column(String)
    status = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow)

class PaymentToken(Base):
    __tablename__ = "payment_tokens"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer)
    token = Column(String, unique=True)
    status = Column(String, default="pending")
    created_at = Column(DateTime, default=datetime.utcnow)

async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
