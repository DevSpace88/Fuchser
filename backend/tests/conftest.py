"""
tests/conftest.py — Shared test setup (pytest fixtures)
=======================================================

A fixture = a function that provides test data/objects. Pytest injects them
into tests by name (dependency injection, similar to FastAPI).

We provide here:
  - A test client (httpx.AsyncClient) for the app.
  - An ISOLATED test database (SQLite in-memory) — not the real Postgres.

Why SQLite for tests?
  - No DB server needed (fast, deterministic).
  - In-memory = volatile; every test starts with a clean DB.
  - WARNING: SQLite does not behave 100% like Postgres (types, constraints).
    For critical migration tests you should use a real Postgres.
    For logic tests (auth flows) SQLite is entirely sufficient.
"""

from collections.abc import AsyncGenerator, AsyncIterator

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlmodel import SQLModel
from sqlmodel.ext.asyncio.session import AsyncSession

# IMPORTANT: import all models so that SQLModel.metadata knows about them
# BEFORE create_all runs.
import app.models  # noqa: F401
from app.core.db import get_session
from app.main import app

# ----------------------------------------------------------------------------
# Test engine: SQLite in-memory. ":memory:" = virtual file name for in-memory.
# `connect_args={"check_same_thread": False}`: otherwise SQLite only allows the
# creating thread; for async/tests other threads must be permitted.
# `staticpool`: shares ONE connection across all sessions in the test ->
# otherwise the in-memory DB would be gone after each session (every
# connection = a new DB).
# ----------------------------------------------------------------------------
TEST_DATABASE_URL = "sqlite+aiosqlite:///:memory:"


@pytest_asyncio.fixture
async def test_engine():
    """Creates the test engine and sets up the tables."""
    engine = create_async_engine(
        TEST_DATABASE_URL,
        echo=False,
        connect_args={"check_same_thread": False},
        poolclass=__import__("sqlalchemy.pool", fromlist=["StaticPool"]).StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def test_session(test_engine) -> AsyncGenerator[AsyncSession, None]:
    """A session on the test DB."""
    TestSessionLocal = async_sessionmaker(
        bind=test_engine, class_=AsyncSession, expire_on_commit=False, autoflush=False
    )
    async with TestSessionLocal() as session:
        yield session


# ----------------------------------------------------------------------------
# Dependency override: redirect get_session to the test session.
# ----------------------------------------------------------------------------
# This is the central trick: FastAPI calls `get_session` on EVERY request.
# We replace it with a function that returns our test session —
# that way the app runs against SQLite instead of Postgres during tests.
@pytest_asyncio.fixture
async def client(test_session: AsyncSession) -> AsyncIterator[AsyncClient]:
    """Test client with the DB dependency overridden."""

    async def _override_get_session():
        # Reuse the same session (so that commits made via the test client
        # are visible to subsequent queries). The in-memory static pool
        # shares the connection anyway.
        yield test_session

    app.dependency_overrides[get_session] = _override_get_session
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()
