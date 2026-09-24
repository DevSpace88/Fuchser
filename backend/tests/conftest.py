"""
tests/conftest.py — Gemeinsames Test-Setup (Pytest Fixtures)
============================================================

Fixture = eine Funktion, die Test-Daten/Objekte bereitstellt. Pytest injiziert
sie per Namen in die Tests (Dependency Injection, ähnlich wie bei FastAPI).

Wir stellen hier bereit:
  - Einen Test-Client (httpx.AsyncClient) für die App.
  - Eine ISOLIERTE Test-Datenbank (SQLite in-memory) — nicht die echte Postgres.

Warum SQLite für Tests?
  - Braucht keinen DB-Server (schnell, deterministisch).
  - In-Memory = flüchtig; jeder Test beginnt mit einer sauberen DB.
  - ACHTUNG: SQLite verhält sich nicht 100% wie Postgres (Typen, Constraints).
    Für kritische Migration-Tests sollte man eine echte Postgres nutzen.
    Für Logik-Tests (Auth-Flows) reicht SQLite völlig.
"""

from collections.abc import AsyncGenerator, AsyncIterator

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlmodel import SQLModel
from sqlmodel.ext.asyncio.session import AsyncSession

# WICHTIG: Alle Modelle importieren, damit SQLModel.metadata sie kennt,
# BEVOR create_all läuft.
import app.models  # noqa: F401
from app.core.db import get_session
from app.main import app

# ----------------------------------------------------------------------------
# Test-Engine: SQLite in-memory. ":memory:" = virtueller Dateiname für In-Memory.
# `connect_args={"check_same_thread": False}`: SQLite erlaubt sonst nur den
# erzeugenden Thread; für async/Tests müssen andere Threads dürfen.
# `staticpool`: teilt EINE Verbindung über alle Sessions im Test -> sonst wäre
# die In-Memory-DB nach jeder Session weg (jede Verbindung = neue DB).
# ----------------------------------------------------------------------------
TEST_DATABASE_URL = "sqlite+aiosqlite:///:memory:"


@pytest_asyncio.fixture
async def test_engine():
    """Erzeugt den Test-Engine und legt Tabellen an."""
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
    """Eine Session auf die Test-DB."""
    TestSessionLocal = async_sessionmaker(
        bind=test_engine, class_=AsyncSession, expire_on_commit=False, autoflush=False
    )
    async with TestSessionLocal() as session:
        yield session


# ----------------------------------------------------------------------------
# Dependency-Override: leite get_session auf die Test-Session um.
# ----------------------------------------------------------------------------
# Das ist der zentrale Trick: FastAPI ruft bei JEDEM Request `get_session` auf.
# Wir ersetzen es durch eine Funktion, die unsere Test-Session zurückgibt —
# damit testet die App gegen SQLite statt gegen Postgres.
@pytest_asyncio.fixture
async def client(test_session: AsyncSession) -> AsyncIterator[AsyncClient]:
    """Test-Client mit überschriebener DB-Dependency."""

    async def _override_get_session():
        # Dieselbe Session wiederverwenden (damit Commits im Test-Client für
        # nachfolgende Queries sichtbar sind). In-Memory-Static-Pool teilt die
        # Verbindung ohnehin.
        yield test_session

    app.dependency_overrides[get_session] = _override_get_session
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()
