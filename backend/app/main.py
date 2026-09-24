"""
main.py — App-Eintrittspunkt: hier entsteht die FastAPI-App
============================================================

Diese Datei macht DREI Dinge:
  1) Lifespan-Setup: beim Start DB-Verbindung pingen + Admin-Seed laufen lassen.
  2) CORS konfigurieren (damit das Frontend uns ansprechen darf).
  3) Den v1-API-Router inkludieren.

Dazu kommt in Produktion das Ausliefern des gebauten Frontends via app.frontend().
"""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1.router import api_router
from app.core.config import settings
from app.core.db import AsyncSessionLocal
from app.core.seed import seed_admin

logger = logging.getLogger(__name__)


# ============================================================================
# 1) LIFESPAN — läuft beim START (und beim STOP) der App
# ============================================================================
# Lifespan ersetzt das alte `@app.on_event("startup")`. Vorteil: Start- UND
# Stop-Logik in EINER Funktion, sauber mit `async with` klappbar.
@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """
    Wird beim Start EINMAL ausgeführt.

    Wir machen hier KEIN `create_all` (Tabellen werden via Alembic-Migration
    angelegt). Dafür:
      - Pingen wir die DB, um frühe Verbindungsfehler zu bemerken.
      - Seeden den Admin-User, falls SEED_ADMIN_* gesetzt.
    """
    logger.info("Starte App in Umgebung '%s'...", settings.environment)

    # --- DB anpingen (sanity check) ---
    from sqlalchemy import text

    try:
        async with AsyncSessionLocal() as session:
            await session.execute(text("SELECT 1"))
        logger.info("DB-Verbindung OK (%s)", settings.database_url.split("@")[-1])
    except Exception as e:
        # Wir brechen NICHT hart ab — in manchen Setups (z. B. Tests ohne DB)
        # ist das OK. Wir loggen nur deutlich.
        logger.warning("DB-Ping fehlgeschlagen: %s", e)

    # --- Admin-Seed ---
    try:
        async with AsyncSessionLocal() as session:
            await seed_admin(session)
    except Exception as e:
        logger.warning("Admin-Seed fehlgeschlagen: %s", e)

    # --- LangGraph-Checkpointer (Deep Research, siehe PLAN.md) ---
    # Legt Checkpoint-Tabellen an + hält die Verbindungen für Threads/State.
    # Schlägt Postgres fehl: Fallback auf InMemorySaver (nur geloggt).
    from app.agent.persistence import init_checkpointer, shutdown_checkpointer

    try:
        app.state.checkpointer = await init_checkpointer()
    except Exception as e:  # noqa: BLE001 — App soll auch ohne Agent-DB laufen
        logger.warning("Checkpointer-Init fehlgeschlagen: %s", e)
        app.state.checkpointer = None

    yield  # <-- ab hier läuft die App und nimmt Requests an

    # Nach yield: Cleanup beim STOPP.
    try:
        await shutdown_checkpointer()
    except Exception as e:  # noqa: BLE001
        logger.warning("Checkpointer-Shutdown fehlgeschlagen: %s", e)
    logger.info("App wird heruntergefahren.")


# ============================================================================
# 2) APP ERZEUGEN
# ============================================================================
app = FastAPI(
    title="Fuchser API",
    description=(
        "Ausführlich kommentiertes FastAPI-Starter-Kit mit JWT-Auth, "
        "Refresh-Token-Rotation, Rollen (RBAC), PostgreSQL + Alembic."
    ),
    version="0.1.0",
    lifespan=lifespan,
    # In Produktion wollen wir die auto-Docs vielleicht abschalten:
    docs_url="/docs" if settings.environment != "prod" else None,
    redoc_url="/redoc" if settings.environment != "prod" else None,
)

# ============================================================================
# 3) CORS — Cross-Origin Resource Sharing
# ============================================================================
# Ein Browser erlaubt per Default KEINE Requests an eine andere Origin als die
# der geladenen Seite. CORS ist der Mechanismus, mit dem wir dem Browser sagen:
# "OK, /api unter dieser Origin darf uns ansprechen."
#
# In Entwicklung: Frontend auf :5173, Backend auf :8000 -> verschiedene Origins
# -> ohne CORS würde der Browser jeden Request blockieren.
#
# In Produktion (wenn das Backend das Frontend mit ausliefert) sind beide unter
# der gleichen Origin -> CORS dann irrelevant.
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,  # wichtig, falls man Cookies nutzt (hier: JWT im Header)
    allow_methods=["*"],
    allow_headers=["*"],
)


# ============================================================================
# 4) ROUTER INKLUDIEREN
# ============================================================================
app.include_router(api_router)


# ============================================================================
# 5) WURZEL-ENGPUNKT (Health/Info)
# ============================================================================
@app.get("/", tags=["meta"])
async def root():
    """Kleiner Info-Endpunkt, damit '/' nicht 404 gibt."""
    return {
        "name": "Fuchser API",
        "version": "0.1.0",
        "docs": "/docs" if settings.environment != "prod" else None,
        "endpoints": {
            "register": "POST /api/v1/auth/register",
            "login": "POST /api/v1/auth/login",
            "refresh": "POST /api/v1/auth/refresh",
            "me": "GET /api/v1/auth/me",
        },
    }


@app.get("/health", tags=["meta"])
async def health():
    """Healthcheck für Docker/Load-Balancer. Sollte immer 200 liefern."""
    return {"status": "ok"}


# ============================================================================
# 6) PRODUKTION: Frontend ausliefern (vom Vite-Build)
# ============================================================================
# `app.frontend(...)` ist FastAPIs eingebaute Methode, ein gebautes Frontend
# (z. B. das `dist/`-Verzeichnis eines Vite-Builds) auszuliefern — inklusive
# SPA-Routing-Fallback (damit React-Routen wie /dashboard direkt funktionieren).
#
# Wir aktivieren das NUR, wenn ein gebautes Frontend existiert. In Entwicklung
# läuft Vite separat (siehe docker-compose.yml).
_frontend_dist = Path(__file__).resolve().parent.parent / "frontend_dist"
if _frontend_dist.is_dir():
    # app.frontend hat niedrige Priorität: API-Routen werden zuerst gematcht,
    # danach erst Frontend-Dateien. So kollidieren /api/* und / nicht.
    app.frontend("/", directory=str(_frontend_dist))
    logger.info("Frontend wird ausgeliefert aus %s", _frontend_dist)


# ============================================================================
# 7) LOKALER START (ohne uvicorn-Kommando)
# ============================================================================
# Für `python -m app.main` oder IDE-Run-Knöpfe. In Docker nutzt man direkter
# `uvicorn app.main:app --reload` (siehe Dockerfile / compose).
if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host="127.0.0.1",
        port=8000,
        reload=settings.environment == "dev",
    )
