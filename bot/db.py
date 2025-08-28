from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker, declarative_base
from sqlalchemy import Column, Integer, String

DATABASE_URL = "sqlite+aiosqlite:///./db.sqlite3"

engine = create_async_engine(DATABASE_URL, echo=True)
async_sessionmaker = sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
)

Base = declarative_base()

class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, unique=True, index=True)
    balance = Column(Integer, default=0)

async def get_or_create_user(session: AsyncSession, user_id: int, username: str = None):
    user = await session.get(User, user_id)
    if not user:
        user = User(id=user_id, username=username or f"user{user_id}")
        session.add(user)
        await session.commit()
        await session.refresh(user)
    return user
