"""
core/db.py — Datenbank-Anbindung (SQLAlchemy async + SQLModel)
==============================================================

Hier definieren wir ZENTRAL:
  1) Den async-Engine (Verbindungspool zur Postgres-DB).
  2) Einen FastAPI-Dependency `get_session`, der pro Request eine Session
     öffnet und danach automatisch schließt.

Konzept: Warum Async?
---------------------
FastAPI ist von Grund auf async. Postgres über asyncpg ist non-blocking.
Das heißt: Während ein Request auf die DB wartet, kann FastAPI andere
Requests bedienen -> hohe Parallelität mit wenig Threads.

Konzept: Dependency Injection (DI)
----------------------------------
Anstatt in JEDEM Endpunkt manuell eine Session zu öffnen (Boilerplate,
Fehleranfällig), definieren wir `get_session` EINMAL. FastAPI injiziert
es automatisch in jeden Endpunkt, der `SessionDep` als Parameter hat.

    async def list_users(session: SessionDep):  # <-- das ist die Magie
        ...

WICHTIG: Die Dependency nutzt `yield` (kein `return`!). Das bedeutet:
FastAPI führt die Funktion bis zum yield aus, gibt die Session an den
Endpunkt weiter, und NACH dem Endpunkt läuft der Code hinter yield
(hier `await session.close()`) als Cleanup. So wird die Session immer
geschlossen — auch bei Exceptions.
"""

from collections.abc import AsyncGenerator
from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlmodel import SQLModel

# WICHTIG: Das ist die SQLModel-Version der AsyncSession. Sie hat im Gegensatz
# zur reinen SQLAlchemy AsyncSession die .exec()-Methode (typisch für SQLModel).
# Wer .execute() + scalars() bevorzugt, kann auch sqlalchemy.ext.asyncio nutzen.
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.config import settings

# ----------------------------------------------------------------------------
# Engine: verwaltet den Verbindungspool zur Datenbank.
# ----------------------------------------------------------------------------
# `pool_pre_ping=True` prüft jede Verbindung, bevor sie genutzt wird, auf
# Gültigkeit (verhindert "stale connection"-Fehler, die nach Idle-Zeit am
# Pool auftreten können). Standard-Best-Practice.
# `echo=False`: kein SQL-Logging. Für Debugging kurzzeitig auf True setzen.
engine = create_async_engine(
    settings.database_url,
    echo=False,
    pool_pre_ping=True,
    future=True,
)

# ----------------------------------------------------------------------------
# Session-Factory: erzeugt neue Sessions. Wir nutzen die async-Variante.
# `expire_on_commit=False`: nach commit() bleiben die Objekte im Speicher
# nutzbar (sonst würden sie "expired" und bei Zugriff neu geladen werden müssen,
# was mit async knifflig sein kann). Best-Practice für FastAPI + async.
#
# WICHTIG: Wir setzen `class_=AsyncSession` EXPLIZIT. Denn async_sessionmaker
# stammt aus SQLAlchemy und würde sonst eine reine SQLAlchemy-Session erzeugen.
# Die kennt SQLModels praktische `.exec()`-Methode NICHT — wir wollen aber
# `await session.exec(select(User))` schreiben können. Deshalb: SQLModel-Session.
# ----------------------------------------------------------------------------
AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)


# ----------------------------------------------------------------------------
# FastAPI-Dependency: liefert pro Request eine frische Session.
# ----------------------------------------------------------------------------
async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """
    Stellt eine DB-Session für die Dauer eines Requests bereit.

    Verwendung (in einem Endpunkt):

        async def handler(session: SessionDep):
            user = await session.get(User, 1)
            ...

    Ablauf:
      1. `async with AsyncSessionLocal() as session` öffnet die Session.
      2. `yield session` gibt sie an den Endpunkt weiter.
      3. Nach dem Endpunkt ( egal ob Erfolg oder Exception ) wird der
         `async with`-Block geschlossen -> Session wird automatisch
         freigegeben.
    """
    async with AsyncSessionLocal() as session:
        yield session


# Typ-Alias, den wir in Endpunkten verwenden. Sieht sauberer aus als die
# lange Annotated-...-Schreibweise und ist WIEDERVERWENDBAR.
# (Empfehlung der FastAPI-Skill: immer Type-Aliase für Dependencies erstellen.)
SessionDep = Annotated[AsyncSession, Depends(get_session)]


# ----------------------------------------------------------------------------
# Hilfsfunktion: Tabellen anlegen (NUR für Tests / Erststart ohne Alembic!)
# ----------------------------------------------------------------------------
# In Produktion übernehmen Alembic-Migrations das Anlegen/Ändern der Tabellen.
# Für Tests mit SQLite-In-Memory nutzen wir diese Funktion aber als Shortcut.
async def create_db_and_tables() -> None:
    """Legt alle Tabellen an, die SQLModel kennt. Nur für Dev/Tests gedacht."""
    # SQLModel.metadata sammelt alle Tabellen-Modelle, die importiert wurden.
    # Wichtig: Alle Modelle müssen importiert sein, bevor man das aufruft!
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
