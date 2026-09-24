"""
api/v1/router.py — aggregates all v1 sub-routers
=================================================

This file is the ONE place where the API structure comes together.
In app/main.py we include exactly THIS router.

Advantage: you add new features (e.g. /api/v1/items) as a new file in api/v1/
and include it here. main.py stays lean.
"""

from fastapi import APIRouter

from app.api.v1 import auth, conversations, research, users

# Parent router with the /api/v1 prefix. Its `include_router(...)` needs NO
# further arguments because the children already carry their own prefixes:
#   auth router  -> /auth
#   users router -> /users
# Combined with the parent prefix: /api/v1/auth/..., /api/v1/users/...
api_router = APIRouter(prefix="/api/v1")
api_router.include_router(auth.router)
api_router.include_router(users.router)
api_router.include_router(research.router)
api_router.include_router(conversations.router)
