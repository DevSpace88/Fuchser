"""
alembic/env.py — Alembic configuration (wired to our project)
=============================================================

Alembic is the migration tool for SQLAlchemy/SQLModel. It generates SQL
migrations when the schema (tables, columns, ...) changes.

The concept of migrations
-------------------------
A migration = a Python file (in versions/) with `upgrade()` and
`downgrade()`. `upgrade()` applies a schema change (e.g. CREATE TABLE),
`downgrade()` reverts it (DROP TABLE).

Alembic records in a table `alembic_version` which migrations have
already run. `alembic upgrade head` brings the DB up to the latest state.

What this env.py does:
  1. Loads the settings (DB URL from .env).
  2. Creates a SYNC engine (Alembic runs synchronously, asyncpg does not work here).
  3. Tells Alembic which models it should know (SQLModel.metadata).
  4. Configures "autogenerate" (comparing models vs. DB).

Typical workflow:
  1. Change a model (e.g. add a column in models/user.py).
  2. `alembic revision --autogenerate -m "add column X to user"`
     -> Alembic compares the models with the DB and generates a new migration.
  3. Review the migration (!!!) — autogenerate is not perfect.
  4. `alembic upgrade head` -> apply the migration.
"""

from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool
from sqlmodel import SQLModel

# IMPORTANT: import all models so that SQLModel.metadata picks them up!
# `import app.models` triggers the __init__.py bulk import.
import app.models  # noqa: F401  (collects User, RefreshToken)
from alembic import context

# Import configuration & models so that SQLModel.metadata knows everything.
from app.core.config import settings

# Alembic configuration object (read from alembic.ini).
config = context.config

# Configure logging (if defined in alembic.ini).
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Set the DB URL programmatically from the settings (a single source of truth).
# We use the SYNC URL because Alembic runs synchronously.
config.set_main_option("sqlalchemy.url", settings.sync_database_url)

# "target_metadata" is the STATE OF THE MODELS in Python. Alembic compares
# it with the ACTUAL state in the DB and generates the migration from that.
target_metadata = SQLModel.metadata


def run_migrations_offline() -> None:
    """
    "Offline" mode: Alembic generates SQL without connecting to a real DB.
    Useful for inspecting migration SQL before running it.

    Invocation: alembic upgrade head --sql
    """
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        # Also compare the type (e.g. VARCHAR vs. TEXT), not just existence:
        compare_type=True,
        # Always compare server defaults:
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """
    "Online" mode (default): Alembic connects to the DB and runs
    the migrations there.
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


# On invocation, Alembic decides which function runs depending on the mode.
if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()


# ----------------------------------------------------------------------------
# NOTE ON ASYNC:
# Alternatively, env.py could be made async (with async_engine_from_config).
# We deliberately use the SYNC variant because:
#   1. Alembic's async support needs a bit more boilerplate.
#   2. Migrations are one-off setup steps where async gives no
#      performance benefit (no parallel requests).
#   3. For learning purposes SYNC is easier to read.
# The app ITSELF keeps running async (see app/core/db.py).
# ----------------------------------------------------------------------------
