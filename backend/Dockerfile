# ============================================================================
# Dockerfile for the FastAPI backend
# ============================================================================
# Build in two stages so the final image stays small:
#   1) "builder"  — installs dependencies with uv into a dedicated /venv
#   2) "runner"   — copies only the finished /venv + the app code, no uv needed.
#
# This is called a "multi-stage build" and is a Docker best practice.

# ----------------------------------------------------------------------------
# Stage 1: Builder — this is where we install the Python dependencies.
# ----------------------------------------------------------------------------
FROM python:3.12-slim AS builder

# Prevents Python from writing .pyc files (saves space + is unnecessary in a
# container) and disables buffering (log lines show up immediately, not later).
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Install uv: we copy the binary directly from the official image.
# That is faster and safer than pip install uv.
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

# FIRST copy only the dependency files. Why? Docker caches every layer.
# If we change only code, this layer stays cached and the
# (slow) re-installation of the dependencies is skipped.
COPY pyproject.toml uv.lock* ./

# Create a virtual environment in /venv and install the dependencies into it.
# We use `uv sync` (not `uv pip install`): that is the recommended way
# for project dependencies and it automatically evaluates uv.lock.
#   --frozen  -> exactly the versions from uv.lock, no resolution/surprises
#   --no-dev  -> no test/lint dependencies (they should not go into the production image)
#   --active  -> install into the already activated /venv (otherwise uv creates
#                its own .venv, which we do not want)
RUN uv venv /venv && \
    VIRTUAL_ENV=/venv uv sync --frozen --no-dev --active

# ----------------------------------------------------------------------------
# Stage 2: Runner — lean final runtime.
# ----------------------------------------------------------------------------
FROM python:3.12-slim AS runner

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    # Set the PATH so the venv from stage 1 is found.
    PATH="/venv/bin:$PATH"

# Tini as the init process: makes signal handling (SIGTERM) correct,
# important for cleanly shutting down the container.
# hadolint ignore=DL3008 — pinning in slim images is optional.
RUN apt-get update && \
    apt-get install -y --no-install-recommends curl && \
    rm -rf /var/lib/apt/lists/*

# Copy the venv from builder STAGE 1 (fully installed).
COPY --from=builder /venv /venv

WORKDIR /app

# Copy the app code. In development this folder is overlaid via a volume
# (see docker-compose.yml), so code changes take effect immediately.
COPY . .

# Default command: Uvicorn starts the FastAPI app.
# In development docker-compose.yml overrides this with --reload.
#EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
