"""
tests/test_auth.py — End-to-end tests for the auth flow
=======================================================

These tests walk through the ENTIRE path:
  HTTP request -> FastAPI router -> dependency -> service -> DB -> HTTP response.

This way we test not just isolated functions but the interplay
(exactly what can go wrong in production).

Flows under test:
  1) Register -> login -> fetch /me.
  2) Protected route without a token -> 401.
  3) Refresh token rotation: old token invalid after refresh.
  4) Authorization: a normal user gets 403 on /users, an admin gets 200.
"""

import pytest


# ----------------------------------------------------------------------------
# Helper function: build the Authorization header.
# ----------------------------------------------------------------------------
def auth_header(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# ============================================================================
# 1) HAPPY PATH: Register -> Login -> /me
# ============================================================================
@pytest.mark.asyncio
async def test_register_login_me(client):
    # --- Register ---
    resp = await client.post(
        "/api/v1/auth/register",
        json={"email": "alice@example.com", "password": "supersecret", "full_name": "Alice"},
    )
    assert resp.status_code == 201, resp.text
    created = resp.json()
    assert created["email"] == "alice@example.com"
    assert "hashed_password" not in created  # the password must never leak out!
    assert created["role"] == "user"
    assert created["is_active"] is True

    # --- Login ---
    resp = await client.post(
        "/api/v1/auth/login",
        json={"email": "alice@example.com", "password": "supersecret"},
    )
    assert resp.status_code == 200, resp.text
    tokens = resp.json()
    assert "access_token" in tokens
    assert "refresh_token" in tokens
    access = tokens["access_token"]

    # --- /me with the access token ---
    resp = await client.get("/api/v1/auth/me", headers=auth_header(access))
    assert resp.status_code == 200, resp.text
    me = resp.json()
    assert me["email"] == "alice@example.com"


# ============================================================================
# 2) PROTECTED ROUTE WITHOUT A TOKEN -> 401
# ============================================================================
@pytest.mark.asyncio
async def test_protected_without_token(client):
    resp = await client.get("/api/v1/auth/me")
    assert resp.status_code == 401


# ============================================================================
# 3) DUPLICATE-EMAIL -> 409
# ============================================================================
@pytest.mark.asyncio
async def test_register_duplicate_email(client):
    payload = {"email": "bob@example.com", "password": "supersecret"}
    resp1 = await client.post("/api/v1/auth/register", json=payload)
    assert resp1.status_code == 201

    # Second attempt with the same email -> conflict.
    resp2 = await client.post("/api/v1/auth/register", json=payload)
    assert resp2.status_code == 409


# ============================================================================
# 4) LOGIN WITH A WRONG PASSWORD -> 401 (same message as "user not found")
# ============================================================================
@pytest.mark.asyncio
async def test_login_wrong_password(client):
    await client.post(
        "/api/v1/auth/register",
        json={"email": "carol@example.com", "password": "correct-pass"},
    )
    resp = await client.post(
        "/api/v1/auth/login",
        json={"email": "carol@example.com", "password": "wrong-pass"},
    )
    assert resp.status_code == 401


# ============================================================================
# 5) REFRESH + ROTATION: after a refresh the old refresh token is dead.
# ============================================================================
@pytest.mark.asyncio
async def test_refresh_rotation(client):
    # Login -> fetch the token pair.
    await client.post(
        "/api/v1/auth/register",
        json={"email": "dan@example.com", "password": "supersecret"},
    )
    resp = await client.post(
        "/api/v1/auth/login",
        json={"email": "dan@example.com", "password": "supersecret"},
    )
    refresh_old = resp.json()["refresh_token"]

    # Refresh #1 -> new pair.
    resp = await client.post("/api/v1/auth/refresh", json={"refresh_token": refresh_old})
    assert resp.status_code == 200, resp.text
    refresh_new = resp.json()["refresh_token"]
    assert refresh_new != refresh_old

    # Using the OLD one again -> must be 401 (rotation!).
    resp = await client.post("/api/v1/auth/refresh", json={"refresh_token": refresh_old})
    assert resp.status_code == 401

    # The NEW one still works.
    resp = await client.post("/api/v1/auth/refresh", json={"refresh_token": refresh_new})
    assert resp.status_code == 200


# ============================================================================
# 6) AUTHORIZATION: normal user -> 403 on /users; admin -> 200.
# ============================================================================
async def _register_and_login(client, email: str, role: str = "user"):
    """Helper: creates a user (via the service directly, to set the role)."""
    # We use the service directly because the public register endpoint
    # always assigns role=user. This lets us simulate an admin.
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.core.security import hash_password
    from app.models.user import User, UserRole

    # Get the current test session from the override.
    gen = app_get_session_for_tests()
    session: AsyncSession = await gen.__anext__()
    user = User(
        email=email,
        hashed_password=hash_password("supersecret"),
        role=UserRole(role),
        is_active=True,
    )
    session.add(user)
    await session.commit()
    await session.refresh(user)

    resp = await client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": "supersecret"},
    )
    return resp.json()["access_token"]


# Trick to get at the override session in tests (read-only).
async def app_get_session_for_tests():
    from app.core.db import get_session
    from app.main import app

    override = app.dependency_overrides.get(get_session)
    if override is None:
        raise RuntimeError("Dependency-Override nicht gesetzt.")
    gen = override()
    async for s in gen:
        yield s
        return


@pytest.mark.asyncio
async def test_authorization_user_forbidden_admin_allowed(client):
    # A normal user tries to fetch /users -> 403.
    user_token = await _register_and_login(client, "user1@example.com", "user")
    resp = await client.get("/api/v1/users", headers=auth_header(user_token))
    assert resp.status_code == 403

    # The admin is allowed to.
    admin_token = await _register_and_login(client, "admin1@example.com", "admin")
    resp = await client.get("/api/v1/users", headers=auth_header(admin_token))
    assert resp.status_code == 200, resp.text
    assert isinstance(resp.json(), list)
