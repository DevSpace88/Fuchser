"""
api/v1/users.py — User-Verwaltung (/api/v1/users/*)
====================================================

Dieser Router demonstriert AUTORISIERUNG im Gegensatz zur bloßen
Authentifizierung.

Endpunkte:
  GET /users         -> Liste aller User (NUR ADMIN)
  GET /users/{id}    -> einzelner User (NUR ADMIN)

Vergleich zu /auth/me (in auth.py):
  /auth/me nutzt CurrentUserDep  -> jeder eingeloggte User darf.
  /users   nutzt RequireAdmin    -> nur eingeloggte User mit Rolle ADMIN.
"""

from uuid import UUID

from fastapi import APIRouter, HTTPException, status
from sqlmodel import select

from app.core.db import SessionDep
from app.core.deps import RequireAdmin
from app.models.user import User
from app.schemas.auth import UserRead

router = APIRouter(prefix="/users", tags=["users"])


# ----------------------------------------------------------------------------
# GET /users — alle User auflisten (NUR ADMIN)
# ----------------------------------------------------------------------------
# `dependencies=[RequireAdmin]` ist der entscheidende Punkt:
# Bevor dieser Endpunkt läuft, führt FastAPI RequireAdmin = Depends(get_current_admin_user)
# aus. Das prüft: Token gültig? User aktiv? Rolle == ADMIN?
# Schlägt etwas fehl, bekommt der Client 401/403 — der Code hier läuft nie.
#
# Man kann die Dependency auch als PARAMETER injizieren lassen, wenn man den
# User im Endpunkt braucht (z. B. um "nur die eigenen Daten" zu filtern):
#   async def list_users(session: SessionDep, _: CurrentUserDep): ...
# Da wir hier den User nicht brauchen, reicht `dependencies=[...]`.
@router.get("", response_model=list[UserRead], dependencies=[RequireAdmin])
async def list_users(session: SessionDep):
    """
    Gibt alle registrierten User zurück. ADMIN-ONLY.

    select(User) entspricht `SELECT * FROM users`. `exec(...).all()` liefert
    eine Liste. Wir geben UserRead zurück (response_model), sodass Passwörter
    automatisch weggefiltert werden.
    """
    users = (await session.exec(select(User).order_by(User.created_at.desc()))).all()
    return users


# ----------------------------------------------------------------------------
# GET /users/{user_id} — einzelner User (NUR ADMIN)
# ----------------------------------------------------------------------------
@router.get("/{user_id}", response_model=UserRead, dependencies=[RequireAdmin])
async def get_user(user_id: UUID, session: SessionDep):
    """
    Gibt einen User anhand seiner UUID zurück. ADMIN-ONLY.

    session.get(User, id) ist der ORM-Weg für `SELECT ... WHERE id = ?`.
    """
    user = await session.get(User, user_id)
    if user is None:
        # 404 = Not Found. Sauberer als 400, weil die Ressource wirklich fehlt.
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User nicht gefunden.",
        )
    return user
