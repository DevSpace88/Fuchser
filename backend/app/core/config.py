"""
core/config.py — Zentrale Konfiguration aus Umgebungsvariablen (.env)
=====================================================================

WARUM diese Datei?
------------------
Früher (und im alten Tutorial `legacy/main_tutorial.py`) standen Secret-Key,
Algorithmus etc. HARDCODED im Quellcode. Das ist gefährlich:

  * Secrets landen in git -> jeder Entwickler & jeder Fork hat sie.
  * Für Dev/Prod braucht man denselben Code mit anderen Werten -> unlösbar.

Die Lösung: Konfiguration über **Umgebungsvariablen** (environment variables).
In Entwicklung legen wir sie in eine `.env`-Datei, in Produktion setzt man sie
im Hosting (z. B. Docker-Compose, Kubernetes, Railway, ...).

`pydantic-settings` macht das komfortabel:
  * Liest automatisch aus .env + echten Umgebungsvariablen.
  * Typ-Validierung (eine ungültige Port-Nummer wirft sofort einen Fehler).
  * Default-Werte für Entwicklung.

WO man was findet:
  * Die `.env`-Datei liegt im Wurzelverzeichnis (siehe `.env.example`).
  * Docker-Compose reicht die Variablen an den Container weiter
    (siehe docker-compose.yml -> `environment:`).
"""

from functools import lru_cache
from typing import Literal

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """
    Alle Konfigurationswerte unserer App, typisiert und validiert.

    Konvention: ALLE Werte haben hier einen Default, der für die Entwicklung
    funktioniert. In Produktion werden sie über die .env / Umgebungsvariablen
    überschrieben.
    """

    # ---- pydantic-settings: wie soll geladen werden? ----------------------
    # model_config ersetzt die alte class Config:
    #   env_file      -> lese Werte aus dieser .env-Datei
    #   env_file_encoding -> Zeichensatz der .env
    #   case_sensitive -> "SECRET_KEY" != "secret_key" (case-sensitiv)
    #   extra="ignore"  -> unbekannte Variablen in .env ignorieren (nicht crashen)
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ---- Allgemeines ------------------------------------------------------
    # In welcher Umgebung laufen wir? Bestimmt z. B. Debug-Verhalten.
    environment: Literal["dev", "prod", "test"] = "dev"

    # ---- Security / JWT ---------------------------------------------------
    # Secret-Key zum Signieren der JWTs. In Produktion PFLICHT (siehe Validator).
    secret_key: str = "dev-only-not-secret-replace-in-production"
    # Signatur-Algorithmus. HS256 = HMAC mit symmetrischem Schlüssel.
    jwt_algorithm: str = "HS256"
    # Lebensdauer Access- / Refresh-Token.
    access_token_expire_minutes: int = 15
    refresh_token_expire_days: int = 7

    # ---- Datenbank (PostgreSQL) ------------------------------------------
    postgres_user: str = "app_user"
    postgres_password: str = "change_me"
    postgres_db: str = "app_db"
    postgres_host: str = "localhost"  # "db" im Compose-Netz, "localhost" lokal
    postgres_port: int = 5432

    @property
    def database_url(self) -> str:
        """
        Baut die SQLAlchemy-URL für Postgres zusammen.

        Format:
            postgresql+asyncpg://USER:PASSWORD@HOST:PORT/DB

        Das "+asyncpg" sagt SQLAlchemy: nutze den async-Treiber.
        Für Sync (z. B. Alembic) nutzen wir stattdessen "+psycopg" oder
        ohne Treiber-Zusatz (siehe alembic/env.py).
        """
        return (
            f"postgresql+asyncpg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @property
    def sync_database_url(self) -> str:
        """
        Sync-Variante der DB-URL, die Alembic braucht.

        Alembic läuft (mit unserem Setup) synchron; asyncpg geht dort nicht.
        Wir nutzen psycopg3 (sync) als Treiber. Falls asyncpg installiert ist,
        ist psycopg meist auch da — sonst via `uv pip install psycopg` nachholen.
        """
        return (
            f"postgresql+psycopg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    # ---- CORS -------------------------------------------------------------
    # Welche Origins darf der Browser für API-Aufrufe nutzen?
    # In Entwicklung: localhost:5173 (Vite). Kommasepariert in der .env.
    cors_origins: str = "http://localhost:5173,http://localhost:3000"

    @property
    def cors_origins_list(self) -> list[str]:
        """Zerlegt den Kommaseparierten String in eine saubere Liste."""
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    # ---- Agent / LangGraph ("Deep Research"-Klon, siehe PLAN.md) ---------
    # API-Keys leer -> Agent-Features laufen im Echo-/Fallback-Modus
    # (sinnvoll für Tests und den allerersten Start ohne Keys).
    #
    # Provider-Auswahl: "deepseek" (Standard) oder "glm" (Z.ai GLM Coding
    # Plan, internationale API). Es wird automatisch der andere genommen,
    # wenn für den gewählten Provider kein Key konfiguriert ist.
    llm_provider: Literal["deepseek", "glm"] = "deepseek"
    deepseek_api_key: str = ""
    deepseek_model: str = "deepseek-chat"
    glm_api_key: str = ""
    glm_model: str = "glm-5.3"
    # OpenAI-kompatibler Chat-Completions-Endpoint des GLM CODING PLANS
    # (nicht die China-Adresse open.bigmodel.cn!). Siehe docs.z.ai/devpack.
    glm_base_url: str = "https://api.z.ai/api/coding/paas/v4"
    tavily_api_key: str = ""

    # Redis für Background-Tasks (Worker + Progress-PubSub)
    # Coolify: komplette URL via REDIS_URL env, z. B. redis://redis:6379/0
    redis_url: str = "redis://localhost:6379/0"

    @property
    def agent_database_url(self) -> str:
        """
        Reine asyncpg-DSN (OHNE "+asyncpg"-Suffix) für den LangGraph-
        Checkpointer. AsyncPostgresSaver will einen nackten Postgres-String,
        SQLAlchemy-Engines dagegen den Treiber-Suffix (siehe database_url).
        """
        return (
            f"postgresql://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    # ---- Seeding (erster Admin) ------------------------------------------
    # Beim ersten Start legen wir automatisch einen Admin-User an,
    # damit man sich sofort einloggen kann. Bleibt leer -> kein Seeding.
    seed_admin_email: str = ""
    seed_admin_password: str = ""

    # ---- Validierung: In Produktion MUSS ein echter Secret-Key gesetzt sein.
    @field_validator("secret_key")
    @classmethod
    def _secret_key_must_be_set_in_prod(cls, v: str, info) -> str:
        # info.data enthält die bereits geparsten Felder; "environment" steht
        # VOR secret_key in der Klasse, ist also schon bekannt.
        env = info.data.get("environment", "dev")
        if env == "prod" and v.startswith("dev-only"):
            raise ValueError(
                "In Produktion muss SECRET_KEY auf einen echten Zufallswert "
                "gesetzt sein! Erzeuge einen mit: "
                'python -c "import secrets; print(secrets.token_urlsafe(32))"'
            )
        return v


# ----------------------------------------------------------------------------
# settings — das Singleton, das überall importiert wird.
# ----------------------------------------------------------------------------
# Warum ein lru_cache? Beim ersten Aufruf wird Settings() konstruiert
# (liest .env, validiert). Bei jedem weiteren Aufruf liefert der Cache
# DASSELBE Objekt zurück — ohne State-Drift.
@lru_cache
def get_settings() -> Settings:
    return Settings()


# Abkürzung: `from app.core.config import settings` reicht meistens.
settings = get_settings()
