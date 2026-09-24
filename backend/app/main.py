"""
main.py — App entry point: this is where the FastAPI app comes to life
======================================================================

This file does THREE things:
  1) Lifespan setup: at startup, ping the DB + run the admin seed.
  2) Configure CORS (so the frontend is allowed to talk to us).
  3) Include the v1 API router.

Additionally, in production it serves the built frontend via app.frontend().
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
# 1) LIFESPAN — runs at app START (and STOP)
# ============================================================================
# Lifespan replaces the old `@app.on_event("startup")`. Advantage: startup AND
# shutdown logic in ONE function, neatly collapsible with `async with`.
@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """
    Executed ONCE at startup.

    We do NOT run `create_all` here (tables are created via Alembic
    migrations). Instead:
      - We ping the DB to catch connection errors early.
      - We seed the admin user if SEED_ADMIN_* is set.
    """
    logger.info("Starte App in Umgebung '%s'...", settings.environment)

    # --- Ping the DB (sanity check) ---
    from sqlalchemy import text

    try:
        async with AsyncSessionLocal() as session:
            await session.execute(text("SELECT 1"))
        logger.info("DB-Verbindung OK (%s)", settings.database_url.split("@")[-1])
    except Exception as e:
        # We do NOT hard-fail — in some setups (e.g. tests without a DB)
        # that is OK. We just log it clearly.
        logger.warning("DB-Ping fehlgeschlagen: %s", e)

    # --- Admin seed ---
    try:
        async with AsyncSessionLocal() as session:
            await seed_admin(session)
    except Exception as e:
        logger.warning("Admin-Seed fehlgeschlagen: %s", e)

    # --- LangGraph checkpointer (Deep Research, see PLAN.md) ---
    # Creates the checkpoint tables + holds the connections for threads/state.
    # If Postgres fails: fallback to InMemorySaver (only logged).
    from app.agent.persistence import init_checkpointer, shutdown_checkpointer

    try:
        app.state.checkpointer = await init_checkpointer()
    except Exception as e:  # noqa: BLE001 — the app should run even without the agent DB
        logger.warning("Checkpointer-Init fehlgeschlagen: %s", e)
        app.state.checkpointer = None

    yield  # <-- from here on the app is running and accepting requests

    # After yield: cleanup at SHUTDOWN.
    try:
        await shutdown_checkpointer()
    except Exception as e:  # noqa: BLE001
        logger.warning("Checkpointer-Shutdown fehlgeschlagen: %s", e)
    logger.info("App wird heruntergefahren.")


# ============================================================================
# 2) CREATE THE APP
# ============================================================================
app = FastAPI(
    title="Fuchser API",
    description=(
        "Fuchser — AI deep-research assistant: JWT auth, refresh-token "
        "rotation, roles (RBAC), PostgreSQL + Alembic, LangGraph multi-agent "
        "research pipeline with a Redis-backed worker."
    ),
    version="0.1.0",
    lifespan=lifespan,
    # In production we may want to turn off the auto-docs:
    docs_url="/docs" if settings.environment != "prod" else None,
    redoc_url="/redoc" if settings.environment != "prod" else None,
)

# ============================================================================
# 3) CORS — Cross-Origin Resource Sharing
# ============================================================================
# By default a browser does NOT allow requests to an origin other than the one
# of the loaded page. CORS is the mechanism with which we tell the browser:
# "OK, /api under this origin is allowed to talk to us."
#
# In development: frontend on :5173, backend on :8000 -> different origins
# -> without CORS the browser would block every request.
#
# In production (when the backend also serves the frontend) both live under
# the same origin -> CORS is then irrelevant.
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,  # important when using cookies (here: JWT in a header)
    allow_methods=["*"],
    allow_headers=["*"],
)


# ============================================================================
# 4) INCLUDE THE ROUTER
# ============================================================================
app.include_router(api_router)


# ============================================================================
# 5) ROOT ENDPOINT (health/info)
# ============================================================================
@app.get("/", tags=["meta"])
async def root():
    """Small info endpoint so '/' does not return 404."""
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
    """Healthcheck for Docker/load balancers. Should always return 200."""
    return {"status": "ok"}


# ============================================================================
# 6) PRODUCTION: serve the frontend (from the Vite build)
# ============================================================================
# `app.frontend(...)` is FastAPI's built-in method for serving a built frontend
# (e.g. the `dist/` directory of a Vite build) — including the SPA routing
# fallback (so React routes like /dashboard work directly).
#
# We only enable this when a built frontend exists. In development
# Vite runs separately (see docker-compose.yml).
_frontend_dist = Path(__file__).resolve().parent.parent / "frontend_dist"
if _frontend_dist.is_dir():
    # app.frontend has low priority: API routes are matched first,
    # and only then frontend files. This way /api/* and / never collide.
    app.frontend("/", directory=str(_frontend_dist))
    logger.info("Frontend wird ausgeliefert aus %s", _frontend_dist)


# ============================================================================
# 7) LOCAL STARTUP (without the uvicorn command)
# ============================================================================
# For `python -m app.main` or IDE run buttons. In Docker, one uses
# `uvicorn app.main:app --reload` directly (see Dockerfile / compose).
if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host="127.0.0.1",
        port=8000,
        reload=settings.environment == "dev",
    )
