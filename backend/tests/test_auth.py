"""
tests/test_auth.py — End-to-End-Tests für den Auth-Flow
=======================================================

Diese Tests gehen den GESAMTEN Pfad durch:
  HTTP-Request -> FastAPI-Router -> Dependency -> Service -> DB -> HTTP-Response.

So testen wir nicht nur isolierte Funktionen, sondern das Zusammenspiel
(genau das, was in Produktion schiefgehen kann).

Getestete Flows:
  1) Registrieren -> Login -> /me abrufen.
  2) Geschützte Route ohne Token -> 401.
  3) Refresh-Token-Rotation: altes Token nach Refresh ungültig.
  4) Autorisierung: normaler User bekommt 403 auf /users, Admin bekommt 200.
"""

import pytest


# ----------------------------------------------------------------------------
# Hilfsfunktion: Authorization-Header bauen.
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
    assert "hashed_password" not in created  # Passwort darf nie raus!
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

    # --- /me mit Access-Token ---
    resp = await client.get("/api/v1/auth/me", headers=auth_header(access))
    assert resp.status_code == 200, resp.text
    me = resp.json()
    assert me["email"] == "alice@example.com"


# ============================================================================
# 2) GESCHÜTZTE ROUTE OHNE TOKEN -> 401
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

    # Zweiter Versuch mit gleicher Mail -> Konflikt.
    resp2 = await client.post("/api/v1/auth/register", json=payload)
    assert resp2.status_code == 409


# ============================================================================
# 4) LOGIN MIT FALSCHEM PASSWORT -> 401 (gleiche Meldung wie "User nicht da")
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
# 5) REFRESH + ROTATION: nach Refresh ist der alte Refresh-Token tot.
# ============================================================================
@pytest.mark.asyncio
async def test_refresh_rotation(client):
    # Login -> Token-Paar holen.
    await client.post(
        "/api/v1/auth/register",
        json={"email": "dan@example.com", "password": "supersecret"},
    )
    resp = await client.post(
        "/api/v1/auth/login",
        json={"email": "dan@example.com", "password": "supersecret"},
    )
    refresh_old = resp.json()["refresh_token"]

    # Refresh #1 -> neues Paar.
    resp = await client.post("/api/v1/auth/refresh", json={"refresh_token": refresh_old})
    assert resp.status_code == 200, resp.text
    refresh_new = resp.json()["refresh_token"]
    assert refresh_new != refresh_old

    # Nochmal mit dem ALTEN -> muss 401 sein (Rotation!).
    resp = await client.post("/api/v1/auth/refresh", json={"refresh_token": refresh_old})
    assert resp.status_code == 401

    # Mit dem NEUEN klappt's nochmal.
    resp = await client.post("/api/v1/auth/refresh", json={"refresh_token": refresh_new})
    assert resp.status_code == 200


# ============================================================================
# 6) AUTORISIERUNG: Normaler User -> 403 auf /users; Admin -> 200.
# ============================================================================
async def _register_and_login(client, email: str, role: str = "user"):
    """Helper: legt einen User an (per Service direkt, um Rolle zu setzen)."""
    # Wir nutzen den Service direkt, weil der öffentliche Register-Endpunkt
    # immer Rolle=user vergibt. So können wir einen Admin simulieren.
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.core.security import hash_password
    from app.models.user import User, UserRole

    # Hole die aktuelle Test-Session aus dem Override.
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


# Trick, um in Tests an die Override-Session zu kommen (lesend).
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
    # Normaler User versucht /users abzurufen -> 403.
    user_token = await _register_and_login(client, "user1@example.com", "user")
    resp = await client.get("/api/v1/users", headers=auth_header(user_token))
    assert resp.status_code == 403

    # Admin darf.
    admin_token = await _register_and_login(client, "admin1@example.com", "admin")
    resp = await client.get("/api/v1/users", headers=auth_header(admin_token))
    assert resp.status_code == 200, resp.text
    assert isinstance(resp.json(), list)
