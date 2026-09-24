"""
api/v1/users.py — user management (/api/v1/users/*)
====================================================

This router demonstrates AUTHORIZATION as opposed to mere
authentication.

Endpoints:
  GET /users         -> list of all users (ADMIN ONLY)
  GET /users/{id}    -> single user (ADMIN ONLY)

Comparison with /auth/me (in auth.py):
  /auth/me uses CurrentUserDep  -> any logged-in user may access it.
  /users   uses RequireAdmin    -> only logged-in users with the ADMIN role.
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
# GET /users — list all users (ADMIN ONLY)
# ----------------------------------------------------------------------------
# `dependencies=[RequireAdmin]` is the crucial point:
# Before this endpoint runs, FastAPI executes RequireAdmin = Depends(get_current_admin_user).
# That checks: token valid? user active? role == ADMIN?
# If anything fails, the client gets 401/403 — the code here never runs.
#
# You can also have the dependency injected as a PARAMETER if you need the
# user inside the endpoint (e.g. to filter for "own data only"):
#   async def list_users(session: SessionDep, _: CurrentUserDep): ...
# Since we do not need the user here, `dependencies=[...]` is enough.
@router.get("", response_model=list[UserRead], dependencies=[RequireAdmin])
async def list_users(session: SessionDep):
    """
    Returns all registered users. ADMIN ONLY.

    select(User) corresponds to `SELECT * FROM users`. `exec(...).all()`
    returns a list. We return UserRead (response_model) so that passwords
    are filtered out automatically.
    """
    users = (await session.exec(select(User).order_by(User.created_at.desc()))).all()
    return users


# ----------------------------------------------------------------------------
# GET /users/{user_id} — single user (ADMIN ONLY)
# ----------------------------------------------------------------------------
@router.get("/{user_id}", response_model=UserRead, dependencies=[RequireAdmin])
async def get_user(user_id: UUID, session: SessionDep):
    """
    Returns a user by UUID. ADMIN ONLY.

    session.get(User, id) is the ORM way of doing `SELECT ... WHERE id = ?`.
    """
    user = await session.get(User, user_id)
    if user is None:
        # 404 = Not Found. Cleaner than 400 because the resource really is missing.
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User nicht gefunden.",
        )
    return user
