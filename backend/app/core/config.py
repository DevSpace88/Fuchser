"""
core/config.py — Central configuration from environment variables (.env)
========================================================================

WHY this file?
--------------
Earlier (and in the old tutorial `legacy/main_tutorial.py`) the secret key,
algorithm etc. were HARDCODED in the source code. That is dangerous:

  * Secrets end up in git -> every developer & every fork has them.
  * For dev/prod you need the same code with different values -> unsolvable.

The solution: configuration via **environment variables**. In development we
put them into a `.env` file; in production you set them in the hosting
environment (e.g. Docker-Compose, Kubernetes, Railway, ...).

`pydantic-settings` makes this comfortable:
  * Reads automatically from .env + real environment variables.
  * Type validation (an invalid port number immediately raises an error).
  * Default values for development.

WHERE to find what:
  * The `.env` file lives in the root directory (see `.env.example`).
  * Docker-Compose passes the variables through to the container
    (see docker-compose.yml -> `environment:`).
"""

from functools import lru_cache
from typing import Literal

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """
    All configuration values of our app, typed and validated.

    Convention: ALL values have a default here that works for development.
    In production they are overridden via .env / environment variables.
    """

    # ---- pydantic-settings: how should values be loaded? ------------------
    # model_config replaces the old class Config:
    #   env_file         -> read values from this .env file
    #   env_file_encoding -> character set of the .env
    #   case_sensitive   -> "SECRET_KEY" != "secret_key" (case-sensitive)
    #   extra="ignore"   -> ignore unknown variables in .env (don't crash)
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ---- General -----------------------------------------------------------
    # Which environment are we running in? Determines e.g. debug behavior.
    environment: Literal["dev", "prod", "test"] = "dev"

    # ---- Security / JWT ---------------------------------------------------
    # Secret key for signing the JWTs. MANDATORY in production (see validator).
    secret_key: str = "dev-only-not-secret-replace-in-production"
    # Signature algorithm. HS256 = HMAC with a symmetric key.
    jwt_algorithm: str = "HS256"
    # Lifetime of the access / refresh token.
    access_token_expire_minutes: int = 15
    refresh_token_expire_days: int = 7

    # ---- Database (PostgreSQL) ---------------------------------------------
    postgres_user: str = "app_user"
    postgres_password: str = "change_me"
    postgres_db: str = "app_db"
    postgres_host: str = "localhost"  # "db" in the Compose network, "localhost" locally
    postgres_port: int = 5432

    @property
    def database_url(self) -> str:
        """
        Assembles the SQLAlchemy URL for Postgres.

        Format:
            postgresql+asyncpg://USER:PASSWORD@HOST:PORT/DB

        The "+asyncpg" tells SQLAlchemy: use the async driver.
        For sync (e.g. Alembic) we use "+psycopg" instead, or no driver
        suffix at all (see alembic/env.py).
        """
        return (
            f"postgresql+asyncpg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @property
    def sync_database_url(self) -> str:
        """
        Sync variant of the DB URL that Alembic needs.

        Alembic runs synchronously (with our setup); asyncpg does not work
        there. We use psycopg3 (sync) as the driver. If asyncpg is installed,
        psycopg is usually present too — otherwise install it via
        `uv pip install psycopg`.
        """
        return (
            f"postgresql+psycopg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    # ---- CORS --------------------------------------------------------------
    # Which origins may the browser use for API calls?
    # In development: localhost:5173 (Vite). Comma-separated in the .env.
    cors_origins: str = "http://localhost:5173,http://localhost:3000"

    @property
    def cors_origins_list(self) -> list[str]:
        """Splits the comma-separated string into a clean list."""
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    # ---- Agent / LangGraph ("Deep Research" clone, see PLAN.md) ------------
    # Empty API keys -> agent features run in echo/fallback mode
    # (useful for tests and the very first start without keys).
    #
    # Provider choice: "deepseek" (default) or "glm" (Z.ai GLM Coding
    # Plan, international API). The other one is used automatically
    # if no key is configured for the selected provider.
    llm_provider: Literal["deepseek", "glm"] = "deepseek"
    deepseek_api_key: str = ""
    deepseek_model: str = "deepseek-chat"
    glm_api_key: str = ""
    glm_model: str = "glm-5.3"
    # OpenAI-compatible chat-completions endpoint of the GLM CODING PLAN
    # (not the China address open.bigmodel.cn!). See docs.z.ai/devpack.
    glm_base_url: str = "https://api.z.ai/api/coding/paas/v4"
    tavily_api_key: str = ""

    # Redis for background tasks (worker + progress PubSub)
    # Coolify: full URL via REDIS_URL env, e.g. redis://redis:6379/0
    redis_url: str = "redis://localhost:6379/0"

    @property
    def agent_database_url(self) -> str:
        """
        Pure asyncpg DSN (WITHOUT the "+asyncpg" suffix) for the LangGraph
        checkpointer. AsyncPostgresSaver wants a bare Postgres string,
        whereas SQLAlchemy engines want the driver suffix (see database_url).
        """
        return (
            f"postgresql://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    # ---- Seeding (first admin) ---------------------------------------------
    # On first startup we automatically create an admin user so you can
    # log in right away. Left empty -> no seeding.
    seed_admin_email: str = ""
    seed_admin_password: str = ""

    # ---- Validation: in production a real secret key MUST be set.
    @field_validator("secret_key")
    @classmethod
    def _secret_key_must_be_set_in_prod(cls, v: str, info) -> str:
        # info.data contains the already-parsed fields; "environment" is
        # declared BEFORE secret_key in the class, so it is already known.
        env = info.data.get("environment", "dev")
        if env == "prod" and v.startswith("dev-only"):
            raise ValueError(
                "In Produktion muss SECRET_KEY auf einen echten Zufallswert "
                "gesetzt sein! Erzeuge einen mit: "
                'python -c "import secrets; print(secrets.token_urlsafe(32))"'
            )
        return v


# ----------------------------------------------------------------------------
# settings — the singleton that is imported everywhere.
# ----------------------------------------------------------------------------
# Why an lru_cache? On the first call Settings() is constructed
# (reads .env, validates). On every further call the cache returns
# the SAME object — no state drift.
@lru_cache
def get_settings() -> Settings:
    return Settings()


# Shortcut: `from app.core.config import settings` is usually enough.
settings = get_settings()
