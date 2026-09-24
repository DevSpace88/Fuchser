"""
alembic/env.py — Alembic-Konfiguration (an unser Projekt angebunden)
====================================================================

Alembic ist das Migrations-Tool für SQLAlchemy/SQLModel. Es erzeugt SQL-
Migrationen, wenn sich das Schema (Tabellen, Spalten, ...) ändert.

Konzept Migrationen
-------------------
Eine Migration = eine Python-Datei (in versions/) mit `upgrade()` und
`downgrade()`. `upgrade()` wendet eine Schema-Änderung an (z. B. CREATE TABLE),
`downgrade()` macht sie rückgängig (DROP TABLE).

Alembic merkt sich in einer Tabelle `alembic_version`, welche Migrationen
schon gelaufen sind. `alembic upgrade head` bringt die DB auf den neuesten Stand.

Was diese env.py macht:
  1. Lädt die Settings (DB-URL aus .env).
  2. Erzeugt einen SYNC-Engine (Alembic läuft synchron, asyncpg funktioniert hier nicht).
  3. Sagt Alembic, welche Modelle es kennen soll (SQLModel.metadata).
  4. Konfiguriert "autogenerate" (Vergleich Modelle vs. DB).

Typischer Workflow:
  1. Modell ändern (z. B. in models/user.py eine Spalte dazurechnen).
  2. `alembic revision --autogenerate -m "add column X to user"`
     -> Alembic vergleicht Modelle mit DB und erzeugt eine neue Migration.
  3. Migration prüfen (!!!) — autogenerate ist nicht perfekt.
  4. `alembic upgrade head` -> Migration anwenden.
"""

from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool
from sqlmodel import SQLModel

# WICHTIG: Import aller Modelle, damit SQLModel.metadata sie einsammelt!
# `import app.models` löst den __init__.py-Sammelimport aus.
import app.models  # noqa: F401  (sammelt User, RefreshToken ein)
from alembic import context

# Konfiguration & Modelle importieren, damit SQLModel.metadata alles kennt.
from app.core.config import settings

# Alembic-Konfigurationsobjekt (aus alembic.ini gelesen).
config = context.config

# Logging konfigurieren (falls in alembic.ini definiert).
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Setze die DB-URL programmatisch aus den Settings (eine Quelle der Wahrheit).
# Wir nutzen die SYNC-URL, weil Alembic synchron läuft.
config.set_main_option("sqlalchemy.url", settings.sync_database_url)

# Das "target_metadata" ist der STAND DER MODELLE in Python. Alembic vergleicht
# das mit dem IST-STAND in der DB und erzeugt daraus die Migration.
target_metadata = SQLModel.metadata


def run_migrations_offline() -> None:
    """
    "Offline"-Modus: Alembic generiert SQL, ohne eine echte DB zu verbinden.
    Nützlich, um Migrations-SQL vor dem Ausführen zu inspizieren.

    Aufruf: alembic upgrade head --sql
    """
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        # Vergleiche auch den Typ (z. B. VARCHAR vs. TEXT), nicht nur Existenz:
        compare_type=True,
        # Server-Defaults immer vergleichen:
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """
    "Online"-Modus (Standard): Alembic verbindet sich mit der DB und führt
    die Migrationen dort aus.
    """
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            compare_server_default=True,
        )
        with context.begin_transaction():
            context.run_migrations()


# Beim Aufruf entscheidet Alembic je nach Modus, welche Funktion läuft.
if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()


# ----------------------------------------------------------------------------
# HINWEIS ZU ASYNC:
# Alternativ ließe sich env.py async gestalten (mit async_engine_from_config).
# Wir nutzen absichtlich die SYNC-Variante, weil:
#   1. Alembics async-Support ein bisschen mehr Boilerplate braucht.
#   2. Migrations einmalige Setup-Schritte sind, bei denen Async keinen
#      Performance-Vorteil bringt (keine Parallel-Requests).
#   3. Für Lernzwecke SYNC einfacher zu lesen ist.
# Die App SELBST läuft weiter mit async (siehe app/core/db.py).
# ----------------------------------------------------------------------------
