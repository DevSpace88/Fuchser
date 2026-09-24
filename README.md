# Fuchser — FastAPI-Starter-Kit (Lern-Vorlage)

Ein vollständiges, **ausführlich kommentiertes** FastAPI-Starter-Kit, vergleichbar
mit einem Laravel-Starter-Kit (Inertia + React + shadcn) — nur für FastAPI.

Es zeigt, wie man in FastAPI richtig anschließt:

- ✅ **Authentifizierung** (Register/Login via E-Mail + Passwort, JWT)
- ✅ **Access-Token + Refresh-Token** (mit Rotation & DB-Speicherung → revokabel)
- ✅ **Autorisierung** (Rollen `user` / `admin`, Dependencies wie `require_admin`)
- ✅ **Datenbank** (PostgreSQL + **SQLModel** + **Alembic**-Migrations)
- ✅ **Frontend** (React + Vite + TypeScript + **shadcn/ui** + Tailwind)
- ✅ **Docker** (`docker compose up` startet alles)
- ✅ **Tests** (pytest + httpx)

> Der gesamte Code ist mit Lern-Kommentaren auf **Deutsch** versehen.
> Jede Schicht erklärt *warum*, nicht nur *was*.

---

## Schnellstart

```bash
# 1) Umgebungsvariablen anlegen (einmalig)
cp .env.example .env
# SECRET_KEY neu generieren:
python -c "import secrets; print(secrets.token_urlsafe(32))"
# Trage das Ergebnis als SECRET_KEY in die .env ein.

# 2) Alles starten (DB + Backend + Frontend)
docker compose up --build

# 3) Im Browser öffnen:
#    Frontend:  http://localhost:5173
#    API-Docs:  http://localhost:8000/docs
#    Als Admin einloggen mit den Werten aus .env (SEED_ADMIN_*)
```

Das erste Mal dauert länger (Image-Build + npm install). Danach startet es in Sekunden.

---

## Architektur-Überblick

```
┌──────────────────────────────────────────────────────────────┐
│                         Browser                                │
│   http://localhost:5173  (React-SPA, Vite-Dev-Server)         │
└──────────────────────────┬───────────────────────────────────┘
                           │ fetch /api/...  (mit JWT im Header)
                           ▼
┌──────────────────────────────────────────────────────────────┐
│                      Vite-Dev-Proxy                            │
│   /api/* und /token/* werden an das Backend weitergereicht     │
│   (deswegen gleiche Origin im Browser — kein CORS-Problem)     │
└──────────────────────────┬───────────────────────────────────┘
                           ▼
┌──────────────────────────────────────────────────────────────┐
│                FastAPI-Backend  :8000                          │
│   api/v1/auth, api/v1/users                                    │
│   ┌────────────────────────────────────────────────────────┐  │
│   │ Dependencies: get_current_user, require_admin           │  │
│   │ Services:     auth_service (Register/Login/Refresh)     │  │
│   │ Security:     pwdlib-Hashing, PyJWT-Encode/Decode       │  │
│   └────────────────────────────────────────────────────────┘  │
└──────────────────────────┬───────────────────────────────────┘
                           ▼
┌──────────────────────────────────────────────────────────────┐
│                PostgreSQL  :5432                               │
│   Tabellen: users, refresh_tokens  (via SQLModel + Alembic)   │
└──────────────────────────────────────────────────────────────┘
```

---

## Projektstruktur

```
awesome-project/
├── docker-compose.yml        # Orchestriert DB + Backend + Frontend
├── .env.example              # Vorlage für Umgebungsvariablen
├── README.md                 # Diese Datei
├── legacy/                   # Alte, einfache main.py (Tutorial) als Referenz
│
├── backend/
│   ├── Dockerfile            # FastAPI-Image
│   ├── pyproject.toml        # Dependencies (uv)
│   ├── alembic.ini           # Migrations-Konfiguration
│   ├── alembic/              # Migrations (env.py + versions/)
│   ├── app/
│   │   ├── main.py           # App-Erstellung, Router, Lifespan, CORS
│   │   ├── core/
│   │   │   ├── config.py     # Einstellungen aus .env (pydantic-settings)
│   │   │   ├── db.py         # Async-Engine + get_session (Dependency)
│   │   │   ├── security.py   # Hashing (pwdlib) + JWT (PyJWT)
│   │   │   └── deps.py       # Auth-Dependencies (current_user, admin)
│   │   ├── models/           # SQLModel-Tabellen (User, RefreshToken)
│   │   ├── schemas/          # Pydantic-Ein-/Ausgabe-Schemas
│   │   ├── services/         # Geschäftslogik (auth_service)
│   │   └── api/v1/           # HTTP-Endpunkte (auth, users)
│   └── tests/                # pytest + httpx
│
└── frontend/
    ├── Dockerfile            # Vite-Build + nginx für Produktion
    ├── package.json
    ├── vite.config.ts        # Dev-Proxy /api -> Backend
    └── src/
        ├── lib/api.ts        # fetch-Wrapper mit JWT + Auto-Refresh
        ├── lib/auth.tsx      # React-Context für Login-State
        ├── components/ui/    # shadcn/ui-Komponenten
        └── routes/           # Login, Register, Dashboard, Admin
```

---

## Authentifizierung & Autorisierung — wie es funktioniert

### Der Login-Flow (Schritt für Schritt)

1. **User gibt E-Mail + Passwort ein** → React schickt `POST /api/v1/auth/login`.
2. **Backend sucht den User in der DB**, prüft das Passwort mit `pwdlib.verify`.
3. **Bei Erfolg** erzeugt das Backend ZWEI Token:
   - **Access-Token** (kurz, z. B. 15 Min) — für jeden API-Aufruf.
   - **Refresh-Token** (lang, z. B. 7 Tage) — um neue Access-Tokens zu holen,
     ohne sich neu einzuloggen.
4. Der Refresh-Token wird **in der DB gespeichert (gehasht!)** — so kann man
   ihn bei Logout oder Diebstahl-Verdacht widerrufen.

### Jeder geschützte Request

```
Browser:  GET /api/v1/users/me
          Authorization: Bearer <access-token>

Backend:  Dependency `get_current_user` läuft:
            1. Token aus Header holen
            2. JWT-Signatur prüfen (PyJWT.decode)
            3. User aus DB laden
            4. prüfen: ist der User aktiv?
          -> Nur wenn alles OK, kommt der Request im Endpunkt an.
```

### Autorisierung (Rollen)

Neben „wer bist du?" (Authentifizierung) gibt es „darfst du das?" (Autorisierung).
Über die Dependency `require_admin` kann ein Endpunkt nur von Admins aufgerufen
werden. Beispiel in `backend/app/api/v1/users.py`:

```python
@router.get("", dependencies=[Depends(require_admin)])
async def list_users(...): ...
```

Schickt ein normaler User den Request, bekommt er **403 Forbidden** — bevor der
Endpunkt-Code überhaupt läuft.

### Refresh-Token-Rotation

Wenn der Access-Token abläuft, schickt das Frontend den Refresh-Token an
`POST /api/v1/auth/refresh`. Das Backend:
1. Prüft den Refresh-Token (Signatur + DB-Lookup).
2. Stellt ein **neues Token-Paar** aus.
3. Macht den **alten Refresh-Token ungültig** (revoked=True in der DB).

Würde jemand den alten Refresh-Token stehlen, fällt das sofort auf, weil dieser
bei der nächsten Nutzung schon revoked ist.

---

## Häufige Befehle

```bash
# --- Docker ---
docker compose up --build          # alles starten
docker compose down                # stoppen (Daten bleiben)
docker compose down -v             # stoppen + DB-Daten löschen!

# --- Backend (im Container) ---
docker compose exec backend python -m alembic upgrade head      # Migrations
docker compose exec backend python -m alembic revision --autogenerate -m "msg"
docker compose exec backend pytest                              # Tests
docker compose exec backend ruff check .                        # Lint
docker compose exec backend ruff format .                       # Format

# --- Frontend (im Container) ---
docker compose exec frontend npm run build                      # Produktions-Build
docker compose exec frontend npx shadcn@latest add button       # shadcn-Komponente
```

---

## Lernpfad — in welcher Reihenfolge lese ich was?

Wenn du das Template zum Lernen nutzen willst, empfehle ich diese Reihenfolge.
Jede Datei hat ausführliche Header-Kommentare, die Konzepte erklären.

### Backend (Kernkonzepte)

1. **`backend/app/core/config.py`** — Wie lädt man Konfiguration aus `.env`?
   Warum hardcodieren schlecht ist. `pydantic-settings`.
2. **`backend/app/models/base.py`** + **`user.py`** + **`refresh_token.py`** —
   SQLModel: wie Pydantic + SQLAlchemy in einem Modell zusammenkommen.
3. **`backend/app/core/security.py`** — Passwort-Hashing (pwdlib/Argon2) und
   JWT (Aufbau, Signatur, HS256 vs. RS256). **Wichtig: erst verstehen, dann weiter.**
4. **`backend/app/core/db.py`** — Async-Engine + `get_session`-Dependency.
   Warum `yield`-Dependencies + Typ-Alias.
5. **`backend/app/core/deps.py`** — **Der Dreh- und Angelpunkt.** Authentifizierung
   (`get_current_user`) vs. Autorisierung (`get_current_admin_user`). Schritt für Schritt kommentiert.
6. **`backend/app/services/auth_service.py`** — Geschäftslogik: Registrieren,
   Login, Refresh mit Rotation, Logout. Warum Services trennen?
7. **`backend/app/api/v1/auth.py`** — So sieht ein Endpunkt aus: dünn, delegiert an den Service.
8. **`backend/alembic/env.py`** + **`versions/0001_initial.py`** — Was ist eine Migration?
   Wie hängt Alembic an den Models?

### Frontend (JWT aus Sicht der SPA)

9. **`frontend/src/lib/api.ts`** — WICHTIG: Wo speichert man JWTs
   (localStorage vs. httpOnly-Cookie)? Wie funktioniert automatischer Refresh bei 401?
10. **`frontend/src/lib/auth.tsx`** — Wie reicht man den Login-State durch die App (React Context)?
11. **`frontend/src/App.tsx`** — Geschützte Routen (`RequireAuth`, `RequireAdmin`).
12. **`frontend/src/routes/*.tsx`** — Login-, Register-, Dashboard-, Admin-Seite.

### Infrastructure

13. **`docker-compose.yml`** — Wie orchestriert man DB + Backend + Frontend?
14. **`backend/Dockerfile`** — Multi-Stage-Builds, Layer-Caching.
15. **`.env.example`** — Welche Variablen gibt es und warum?

---

## Entwicklung ohne Docker (lokal)

Wenn du das Backend ohne Docker laufen lassen willst (z. B. fürs Debuggen in der IDE):

```bash
# Terminal 1: Postgres (z. B. via Docker, nur die DB)
docker run -d --name awesome_db -p 5432:5432 \
  -e POSTGRES_USER=app_user -e POSTGRES_PASSWORD=change_me \
  -e POSTGRES_DB=app_db postgres:17-alpine

# Terminal 2: Backend
cd backend
cp ../.env.example ../.env
# In .env: POSTGRES_HOST=localhost setzen
uv sync
uv run python -m alembic upgrade head
uv run uvicorn app.main:app --reload

# Terminal 3: Frontend
cd frontend
npm install
npm run dev
```

---

## Tests ausführen

```bash
# Backend-Tests (verwenden SQLite in-memory, brauchen KEINE Postgres)
cd backend
uv run pytest              # alle
uv run pytest -v           # detailliert
uv run pytest -k refresh   # nur Tests mit "refresh" im Namen
```

Die Tests decken ab: Registrierung, Login, /me, Duplicate-Email, falsches Passwort,
Refresh mit Rotation, Autorisierung (User -> 403, Admin -> 200).

---

## Erweiterungen (bewusst weggelassen)

Dieses Template ist absichtlich lernbar gehalten. Für echtes Produktions-Setup
solltest du ergänzen:

- **Passwort-Reset + E-Mail-Verifizierung** (Token-Endpunkt + Mailversand).
- **OAuth2 Social Login** (Google/GitHub via `authlib`).
- **Fine-grained Permissions** (Tabelle `user_permissions` statt nur Rollen).
- **Rate-Limiting** (`slowapi`).
- **Strukturiertes Logging** (`structlog`).
- **httpOnly-Cookie statt localStorage** für Tokens (XSS-sicherer).

---

## Lizenz

MIT — lern, kopier, bau drauf auf. 🚀
