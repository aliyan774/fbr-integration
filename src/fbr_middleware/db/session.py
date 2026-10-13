"""Engine / session factory and startup table creation (Alembic is optional and not wired yet)."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from .models import Base


def _ensure_sqlite_dir(url: str) -> None:
    u = make_url(url)
    if u.get_backend_name() == "sqlite" and u.database and u.database != ":memory:":
        Path(u.database).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)


def make_engine(database_url: str) -> AsyncEngine:
    _ensure_sqlite_dir(database_url)
    kwargs: dict = {"pool_pre_ping": True}
    if make_url(database_url).get_backend_name() == "sqlite":
        # aiosqlite: a single process writes; wait up to 30 s on a locked file.
        kwargs["connect_args"] = {"timeout": 30}
    return create_async_engine(database_url, **kwargs)


def make_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


async def init_db(engine: AsyncEngine) -> None:
    """Create tables if missing. Safe to call on every start."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
