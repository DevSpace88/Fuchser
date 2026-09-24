# ============================================================================
# Dockerfile für das FastAPI-Backend
# ============================================================================
# Build in zwei Stufen, damit das finale Image klein ist:
#   1) "builder"  — installiert Dependencies mit uv in ein eigenes /venv
#   2) "runner"   — kopiert nur das fertige /venv + den App-Code, kein uv nötig.
#
# Das nennt man "Multi-Stage-Build" und ist Docker-Best-Practice.

# ----------------------------------------------------------------------------
# Stage 1: Builder — hier installieren wir die Python-Dependencies.
# ----------------------------------------------------------------------------
FROM python:3.12-slim AS builder

# Verhindert, dass Python .pyc-Dateien schreibt (spart Platz + ist im Container
# unnötig) und schaltet Buffering ab (Log-Zeilen sofort sehen, nicht erst später).
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# uv installieren: Wir holen uns das Binary direkt aus dem offiziellen Image.
# Das ist schneller und sicherer als pip install uv.
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

# ZUERST nur die Dependency-Dateien kopieren. Warum? Docker cacht jede Schicht
# (Layer). Wenn wir nur Code ändern, bleibt dieser Layer gecacht und das
# Neu-Installieren der (langsamen) Dependencies entfällt.
COPY pyproject.toml uv.lock* ./

# Virtuelle Umgebung in /venv anlegen und Dependencies dorthin installieren.
# Wir nutzen `uv sync` (nicht `uv pip install`): das ist der empfohlene Weg
# für Projekt-Dependencies und wertet automatisch uv.lock aus.
#   --frozen  -> exakt die Versionen aus uv.lock, keine Auflösung/Überraschungen
#   --no-dev  -> keine Test-/Lint-Dependencies (sollen nicht ins Produktions-Image)
#   --active  -> in die bereits aktivierte /venv installieren (sonst legt uv eine
#                eigene .venv an, die wir nicht nutzen wollen)
RUN uv venv /venv && \
    VIRTUAL_ENV=/venv uv sync --frozen --no-dev --active

# ----------------------------------------------------------------------------
# Stage 2: Runner — schlanke finale Runtime.
# ----------------------------------------------------------------------------
FROM python:3.12-slim AS runner

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    # Pfad so setzen, dass die venv aus Stage 1 gefunden wird.
    PATH="/venv/bin:$PATH"

# Tini als Init-Prozess: macht Signal-Verarbeitung (SIGTERM) korrekt,
# wichtig für sauberes Herunterfahren des Containers.
# hadolint ignore=DL3008 — pinning in slim-Images ist optional.
RUN apt-get update && \
    apt-get install -y --no-install-recommends curl && \
    rm -rf /var/lib/apt/lists/*

# Die venv aus dem Builder STAGE 1 kopieren (fertig installiert).
COPY --from=builder /venv /venv

WORKDIR /app

# App-Code kopieren. In der Entwicklung wird dieser Ordner via volume
# überlagert (siehe docker-compose.yml), sodass Code-Änderungen sofort greifen.
COPY . .

# Standard-Command: Uvicorn startet die FastAPI-App.
# In Entwicklung überschreibt docker-compose.yml das mit --reload.
#EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
