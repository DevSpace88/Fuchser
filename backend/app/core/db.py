"""
core/db.py — Database integration (SQLAlchemy async + SQLModel)
===============================================================

Here we define, CENTRALLY:
  1) The async engine (connection pool for the Postgres DB).
  2) A FastAPI dependency `get_session` that opens one session per request
     and closes it automatically afterwards.

Concept: Why async?
-------------------
FastAPI is async at its core. Postgres via asyncpg is non-blocking.
That means: while one request waits for the DB, FastAPI can serve other
requests -> high concurrency with few threads.

Concept: Dependency Injection (DI)
----------------------------------
Instead of manually opening a session in EVERY endpoint (boilerplate,
error-prone), we define `get_session` ONCE. FastAPI injects it
automatically into every endpoint that has `SessionDep` as a parameter.

    async def list_users(session: SessionDep):  # <-- this is the magic
        ...

IMPORTANT: The dependency uses `yield` (not `return`!). That means:
FastAPI runs the function up to the yield, hands the session to the
endpoint, and AFTER the endpoint the code behind the yield
(here `await session.close()`) runs as cleanup. This way the session is
always closed — even on exceptions.
"""

from collections.abc import AsyncGenerator
from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlmodel import SQLModel

# IMPORTANT: This is the SQLModel version of AsyncSession. In contrast to the
# pure SQLAlchemy AsyncSession it has the .exec() method (typical for SQLModel).
# If you prefer .execute() + scalars(), you can also use sqlalchemy.ext.asyncio.
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.config import settings

# ----------------------------------------------------------------------------
# Engine: manages the connection pool to the database.
# ----------------------------------------------------------------------------
# `pool_pre_ping=True` checks every connection for validity before it is
# used (prevents "stale connection" errors that can occur after idle time
# at the pool). Standard best practice.
# `echo=False`: no SQL logging. Set to True temporarily for debugging.
engine = create_async_engine(
    settings.database_url,
    echo=False,
    pool_pre_ping=True,
    future=True,
)

# ----------------------------------------------------------------------------
# Session factory: creates new sessions. We use the async variant.
# `expire_on_commit=False`: after commit() the objects remain usable in
# memory (otherwise they would be "expired" and would have to be reloaded on
# access, which can be tricky with async). Best practice for FastAPI + async.
#
# IMPORTANT: We set `class_=AsyncSession` EXPLICITLY. Because async_sessionmaker
# comes from SQLAlchemy and would otherwise create a pure SQLAlchemy session.
# That one does NOT know SQLModel's handy `.exec()` method — but we want to
# be able to write `await session.exec(select(User))`. Hence: SQLModel session.
# ----------------------------------------------------------------------------
AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)


# ----------------------------------------------------------------------------
# FastAPI dependency: provides a fresh session per request.
# ----------------------------------------------------------------------------
async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """
    Provides a DB session for the duration of a request.

    Usage (in an endpoint):

        async def handler(session: SessionDep):
            user = await session.get(User, 1)
            ...

    Flow:
      1. `async with AsyncSessionLocal() as session` opens the session.
      2. `yield session` passes it to the endpoint.
      3. After the endpoint ( whether success or exception ) the
         `async with` block is closed -> the session is automatically
         released.
    """
    async with AsyncSessionLocal() as session:
        yield session


# Type alias that we use in endpoints. Looks cleaner than the
# long Annotated-... notation and is REUSABLE.
# (FastAPI-skill recommendation: always create type aliases for dependencies.)
SessionDep = Annotated[AsyncSession, Depends(get_session)]


# ----------------------------------------------------------------------------
# Helper function: create tables (ONLY for tests / first start without Alembic!)
# ----------------------------------------------------------------------------
# In production, Alembic migrations take care of creating/changing the tables.
# For tests with in-memory SQLite we do use this function as a shortcut.
async def create_db_and_tables() -> None:
    """Creates all tables that SQLModel knows about. Meant for dev/tests only."""
    # SQLModel.metadata collects all table models that have been imported.
    # Important: all models must be imported before you call this!
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
