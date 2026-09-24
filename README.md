# Fuchser — AI Deep-Research Assistant

Fuchser is an AI research assistant: ask a question, and a LangGraph multi-agent
system plans the research, searches the web, and writes a cited report — streaming
every step live to a React frontend.

Built with **FastAPI + SQLModel + PostgreSQL**, **LangGraph**, **Redis**, and
**React + Vite + TypeScript + Tailwind**.

> **Note on language:** the product itself is German (UI, prompts, generated
> reports). Code comments and this README are English.

---

## Features

- **Multi-agent deep research** (LangGraph): a supervisor decomposes your question
  into sub-questions, parallel ReAct researchers search the web (Tavily or
  DuckDuckGo), a synthesizer writes a Markdown report with citations, and a critic
  loop iterates until the quality bar is met (with a revision cap).
- **Deep report mode**: 30+ page reports — outline generation with user approval,
  per-chapter research fan-out, sequential chapter writers, and an APA/IEEE
  bibliography.
- **Live streaming (SSE)**: watch agents work in real time — tokens, sub-questions,
  sources, and the outline as it is produced. The frontend also renders a live
  agent-graph visualization (`@xyflow/react`).
- **Document upload**: attach PDF/DOCX/TXT/MD/CSV files to a project; text is
  extracted and searchable by the researcher agents.
- **Follow-up conversations**: ask questions about a finished report; the agent
  uses the research as context.
- **PDF export**: download finished reports as PDF (xhtml2pdf).
- **Authentication**: JWT access + refresh tokens with rotation and DB-hashed
  (revocable) refresh tokens, roles `user`/`admin`, Argon2 password hashing.
- **Worker architecture**: research runs execute in a separate worker process,
  fed by a Redis queue with PubSub progress events — runs survive API reloads.
  Heartbeats + orphaned-run requeue provide self-healing; chapters are persisted
  incrementally, so interrupted runs can resume cheaply.
- **Switchable LLM provider**: DeepSeek or Z.ai GLM (Coding Plan endpoint) —
  selected via `LLM_PROVIDER`, including token/cost usage tracking.

---

## Architecture

```
                        ┌─────────────────────────────┐
                        │           Browser           │
                        │   React SPA (Vite build)    │
                        └──────────────┬──────────────┘
                                       │ fetch /api/*  (JWT Bearer)
                                       ▼
                 ┌───────────────────────────────────────────┐
                 │  dev: Vite dev proxy    prod: nginx       │
                 │  forwards /api/* to the backend           │
                 └─────────────────────┬─────────────────────┘
                                       ▼
┌─────────────────────────────────────────────────────────────────┐
│                      FastAPI backend (:8000)                     │
│   /api/v1/auth   /api/v1/users   /api/v1/research               │
│   /api/v1/conversations                                          │
│   ┌───────────────────────────────────────────────────────────┐ │
│   │ Dependencies: get_current_user, RequireAdmin              │ │
│   │ Services:     auth · research · document · conversation   │ │
│   │ Security:     pwdlib (Argon2) + PyJWT                     │ │
│   └───────────────────────────────────────────────────────────┘ │
└───────┬─────────────────────────────────────────────┬───────────┘
        │ enqueue run,                                 │ asyncpg
        │ stream progress via PubSub → SSE             ▼
        ▼                                      ┌────────────────────┐
┌──────────────────┐    checkpoints / results  │ PostgreSQL :5432   │
│ Redis :6379      │◄─────────────────────────►│ users, refresh_    │
│ queue + PubSub   │                           │ tokens, research_  │
└────────┬─────────┘                           │ projects, documents│
         │ BLPOP                               │ conversations +    │
         ▼                                     │ LangGraph tables   │
┌───────────────────────────────┐              └────────────────────┘
│ Worker (python -m app.worker) │
│  LangGraph multi-agent graph: │
│  supervisor → researchers →   │
│  synthesizer → critic         │
└───────────────────────────────┘
```

The API stays responsive because the heavy agent work happens in the worker:
`POST /api/v1/research/{id}/run` pushes the run onto a Redis queue and returns an
SSE stream; the worker picks it up, executes the graph, and publishes progress
over Redis PubSub, which the endpoint forwards to the client.

---

## Project structure

```
Fuchser/
├── docker-compose.yml        # Production stack (Coolify): db, redis, backend, worker, frontend
├── .env.example              # Environment variable template
│
├── backend/
│   ├── Dockerfile            # Multi-stage build (uv sync → slim runner)
│   ├── pyproject.toml        # Dependencies (uv), ruff + pytest config
│   ├── alembic.ini
│   ├── alembic/              # Migrations (env.py + 14 versions)
│   ├── scripts/              # Ops helpers (smoke test, source recovery)
│   ├── app/
│   │   ├── main.py           # App factory, lifespan (DB ping, admin seed), CORS
│   │   ├── worker.py         # Standalone worker: Redis queue → LangGraph, heartbeats
│   │   ├── core/             # config, db, security, deps, redis, seed
│   │   ├── models/           # SQLModel tables: user, refresh_token,
│   │   │                     #   research_project, document, conversation
│   │   ├── schemas/          # Pydantic request/response schemas
│   │   ├── services/         # Business logic: auth, research, document, conversation
│   │   ├── api/v1/           # HTTP endpoints (thin, delegate to services)
│   │   └── agent/            # LangGraph deep-research system
│   │       ├── graph.py      #   Multi-agent graph: supervisor → researchers
│   │       │                 #   → synthesizer → critic loop
│   │       ├── deep_report.py#   30+ page pipeline: outline approval
│   │       │                 #   → per-chapter research → chapter writers
│   │       ├── nodes/        #   supervisor, researcher, synthesizer, critic
│   │       ├── tools.py      #   Web search: Tavily (if key) / DuckDuckGo (default)
│   │       ├── llm.py        #   LLM provider switch: DeepSeek ↔ Z.ai GLM
│   │       └── …             #   citations, persistence, usage, events, language
│   └── tests/                # pytest + httpx (SQLite in-memory), 82 tests
│
└── frontend/
    ├── Dockerfile            # Vite build → nginx (proxies /api to the backend)
    ├── vite.config.ts        # Dev proxy: /api → http://localhost:8000
    └── src/
        ├── lib/              # api.ts (JWT + auto-refresh), auth.tsx (context),
        │                     #   sse.ts, citations.ts, research.ts
        ├── components/       # ResearchPanel (live SSE UI), GraphView (agent
        │                     #   graph), ConfirmModal, shadcn-style ui/
        └── routes/           # landing, login, register, dashboard, research,
                              #   research detail, history, admin, 404
```

---

## Quick start (local development)

Prerequisites: Docker (for Postgres + Redis), [uv](https://docs.astral.sh/uv/)
(Python ≥ 3.12), Node.js/npm.

```bash
# 1) Environment
cp .env.example .env
# Generate a real secret and put it in .env as SECRET_KEY:
python -c "import secrets; print(secrets.token_urlsafe(32))"
# For local (non-Docker) backend, also set in .env:
#   POSTGRES_HOST=localhost
#   REDIS_URL=redis://localhost:6379/0
# Research runs need an LLM key: DEEPSEEK_API_KEY (or LLM_PROVIDER=glm + GLM_API_KEY)

# 2) Infrastructure: Postgres + Redis
docker run -d --name fuchser-db -p 5432:5432 \
  -e POSTGRES_USER=app_user -e POSTGRES_PASSWORD=change_me \
  -e POSTGRES_DB=app_db postgres:17-alpine
docker run -d --name fuchser-redis -p 6379:6379 redis:7-alpine

# 3) Backend
cd backend
uv sync
uv run python -m alembic upgrade head
uv run uvicorn app.main:app --reload        # http://localhost:8000/docs

# 4) Worker (separate terminal — required for research runs)
uv run python -m app.worker

# 5) Frontend (separate terminal)
cd ../frontend
npm install
npm run dev                                 # http://localhost:5173
```

The first admin account is seeded automatically on startup from
`SEED_ADMIN_EMAIL` / `SEED_ADMIN_PASSWORD`.

Web search works out of the box via DuckDuckGo (no key needed); set
`TAVILY_API_KEY` for higher-quality Tavily results.

---

## Production deployment

`docker-compose.yml` is a **production stack for Coolify** — not a dev setup.
It starts five services:

| Service  | Purpose                                                        |
| -------- | -------------------------------------------------------------- |
| `db`     | PostgreSQL 17 (volume `fuchser_pgdata`)                        |
| `redis`  | Redis 7 with AOF persistence (queue + PubSub, volume)          |
| `backend`| FastAPI; runs `alembic upgrade head`, then uvicorn (no reload) |
| `worker` | Same image as backend; runs `python -m app.worker`             |
| `frontend` | nginx serving the Vite build, reverse-proxying `/api`        |

All ports are internal (`expose`, no host port mappings) — Coolify's reverse
proxy maps the domains. Swagger/ReDoc docs are disabled when `ENVIRONMENT=prod`.
Default CORS origin is `https://fuchser.sergejgorochow.de` (override via
`CORS_ORIGINS`). Migrations run automatically on backend start.

---

## Configuration

All values live in `.env` (see `.env.example` for the annotated template).

**General / database / auth**

| Variable | Default | Purpose |
| -------- | ------- | ------- |
| `ENVIRONMENT` | `dev` | `dev` / `prod` / `test` (prod disables docs, strict CORS) |
| `POSTGRES_USER` / `_PASSWORD` / `_DB` | `app_user` / … / `app_db` | Database credentials |
| `POSTGRES_HOST` / `_PORT` | `db` / `5432` | `db` inside compose, `localhost` for local dev |
| `SECRET_KEY` | — | JWT signing key; a strong random value is required in prod |
| `JWT_ALGORITHM` | `HS256` | HMAC with symmetric key (see `core/security.py` for RS256 notes) |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | `15` | Access-token lifetime (short = safer) |
| `REFRESH_TOKEN_EXPIRE_DAYS` | `7` | Refresh-token lifetime |
| `CORS_ORIGINS` | `http://localhost:5173,…` | Comma-separated allowed origins |
| `SEED_ADMIN_EMAIL` / `_PASSWORD` | — | First admin account, seeded at startup |
| `REDIS_URL` | `redis://redis:6379/0` | Queue + PubSub for background runs |

**Agent / LLM**

| Variable | Default | Purpose |
| -------- | ------- | ------- |
| `LLM_PROVIDER` | `deepseek` | `deepseek` or `glm` |
| `DEEPSEEK_API_KEY` | — | DeepSeek API key (platform.deepseek.com) |
| `DEEPSEEK_MODEL` | `deepseek-chat` | DeepSeek V3 with tool calling |
| `GLM_API_KEY` | — | Z.ai GLM Coding Plan key (api.z.ai, not bigmodel.cn) |
| `GLM_MODEL` | `glm-5.3` | Or `glm-5.3-flash` (faster/cheaper) |
| `GLM_BASE_URL` | `https://api.z.ai/api/coding/paas/v4` | Coding-Plan endpoint |
| `TAVILY_API_KEY` | — | Enables Tavily search; DuckDuckGo is the keyless default |

---

## API overview

All endpoints are prefixed with `/api/v1`.

| Area | Endpoints |
| ---- | --------- |
| Auth | `POST /auth/register`, `POST /auth/login`, `POST /auth/login/oauth` (form for Swagger), `POST /auth/refresh`, `POST /auth/logout`, `GET /auth/me` |
| Users (admin) | `GET /users`, `GET /users/{id}` |
| Research | `POST /research`, `GET /research`, `GET /research/{id}`, `PATCH /research/{id}`, `DELETE /research/{id}`, `GET /research/admin/all` (admin), `GET /research/{id}/pdf` |
| Research runs | `POST /research/{id}/run` (SSE), `POST /research/{id}/resume` (SSE, continue after outline approval) |
| Documents | `POST /research/{id}/documents` (upload), `GET /research/{id}/documents`, `DELETE /research/{id}/documents/{doc_id}` |
| Conversations | `GET /conversations`, `GET /conversations/{id}`, `PATCH /conversations/{id}`, `DELETE /conversations/{id}` |

---

## Tests, linting, formatting

The test suite (82 tests) runs against SQLite in-memory — **no Postgres/Redis
needed**. It covers auth (register, login, refresh rotation, roles), the agent
graph, deep-report pipeline, LLM handling, document upload, and language
processing.

```bash
# Backend (from backend/)
uv run pytest              # all tests
uv run pytest -v           # verbose
uv run pytest -k refresh   # only tests matching "refresh"
uv run ruff check .        # lint
uv run ruff format .       # format

# Frontend (from frontend/)
npm run build              # type-check (tsc) + production build
```

> Note: run these locally. The production backend image installs dependencies
> with `--no-dev` (no pytest/ruff), and the frontend container is nginx (no node).

---

## License

MIT — learn from it, copy it, build on it. 🚀
