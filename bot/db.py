from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker, declarative_base
from sqlalchemy import Column, Integer, String, Float, DateTime
from sqlalchemy.sql import func

DATABASE_URL = "sqlite+aiosqlite:///./bot.sqlite3"

engine = create_async_engine(DATABASE_URL, echo=True)
Session = sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
)

Base = declarative_base()

# Пользователи
class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, unique=True, index=True)
    balance = Column(Integer, default=0)

# Подписки
class Subscription(Base):
    __tablename__ = "subscriptions"
    user_id = Column(Integer, primary_key=True, index=True)
    status = Column(String, default="expired")
    paid_until = Column(DateTime, nullable=True)
    grace_until = Column(DateTime, nullable=True)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

# Платежи
class Payment(Base):
    __tablename__ = "payments"
    order_ref = Column(String, primary_key=True, index=True)
    user_id = Column(Integer, index=True)
    amount = Column(Float)
    currency = Column(String)
    status = Column(String)
    created_at = Column(DateTime, default=func.now())

# Токены платежей
class PaymentToken(Base):
    __tablename__ = "payment_tokens"
    token = Column(String, primary_key=True, index=True)
    user_id = Column(Integer, index=True)
    status = Column(String, default="pending")
    created_at = Column(DateTime, default=func.now())


async def get_or_create_user(session: AsyncSession, user_id: int, username: str = None):
    user = await session.get(User, user_id)
    if not user:
        user = User(id=user_id, username=username or f"user{user_id}")
        session.add(user)
        await session.commit()
        await session.refresh(user)
    return user


async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
