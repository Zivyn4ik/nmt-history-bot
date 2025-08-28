from datetime import datetime
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import declarative_base, sessionmaker, Mapped, mapped_column
from sqlalchemy import String, DateTime

from config import settings

Base = declarative_base()

class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)  # Telegram ID == PK
    status: Mapped[str] = mapped_column(String(16), default="INACTIVE", index=True)  # ACTIVE/INACTIVE
    start_date: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=False), nullable=True)
    end_date: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=False), nullable=True)
    order_reference: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=False), default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=False), default=datetime.utcnow, onupdate=datetime.utcnow)

engine = create_async_engine(settings.DATABASE_URL, echo=False, future=True)
async_session_maker = sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

async def init_db() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

async def get_or_create_user(session: AsyncSession, tg_id: int) -> User:
    user = await session.get(User, tg_id)
    if not user:
        user = User(id=tg_id, status="INACTIVE")
        session.add(user)
        await session.commit()
    return user
