"""
api/v1/router.py — fasst alle v1-Sub-Router zusammen
=====================================================

Diese Datei ist die EINE Stelle, an der die API-Struktur zusammenkommt.
In app/main.py inkludieren wir genau DIESEN Router.

Vorteil: Neue Features (z. B. /api/v1/items) legst du als neue Datei in api/v1/
an und inkludierst sie hier. main.py bleibt schlank.
"""

from fastapi import APIRouter

from app.api.v1 import auth, conversations, research, users

# Eltern-Router mit Prefix /api/v1. Dessen `include_router(...)` braucht KEINE
# weiteren Argumente, weil die Kinder bereits eigene Prefixe tragen:
#   auth-Router  -> /auth
#   users-Router -> /users
# Zusammen mit dem Eltern-Prefix: /api/v1/auth/..., /api/v1/users/...
api_router = APIRouter(prefix="/api/v1")
api_router.include_router(auth.router)
api_router.include_router(users.router)
api_router.include_router(research.router)
api_router.include_router(conversations.router)
