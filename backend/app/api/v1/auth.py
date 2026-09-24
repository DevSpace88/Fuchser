"""
api/v1/auth.py — Auth-Endpunkte (/api/v1/auth/*)
================================================

Das ist die "Tür" unserer App: hier kann man sich registrieren, einloggen,
Tokens erneuern und ausloggen.

Endpunkte:
  POST /auth/register        -> neuen User anlegen (öffentlich)
  POST /auth/login           -> einloggen, bekommt Token-Paar (JSON-Eingabe)
  POST /auth/login/oauth     -> gleicher Login, aber OAuth2-konform (FORMULAR-Eingabe)
                                -> das ist der Endpunkt, den der /docs-Authorize-Button nutzt
  POST /auth/refresh         -> neues Token-Paar (Rotation)
  POST /auth/logout          -> Refresh-Token widerrufen
  GET  /auth/me              -> aktuell eingeloggten User (geschützt)

Konzepthinweis — warum ZWEI Login-Endpunkte?
--------------------------------------------
FastAPI's Swagger-UI (/docs) hat einen "Authorize"-Button, der das Standard-
OAuth2-Formular (username/password als FORM-DATA) an einen Endpunkt schickt.
Damit der Button funktioniert, brauchen wir einen Endpunkt mit
OAuth2PasswordRequestForm.

Für unser eigenes Frontend ist JSON bequemer (EmailStr + Password als JSON-Body),
daher zusätzlich /auth/login (JSON).

Beide rufen denselben auth_service.authenticate auf — DRY.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordRequestForm

from app.core.db import SessionDep
from app.core.deps import CurrentUserDep
from app.schemas.auth import (
    RefreshIn,
    TokenPair,
    UserCreate,
    UserLogin,
    UserRead,
)
from app.services.auth_service import (
    EmailAlreadyExistsError,
    InvalidCredentialsError,
    authenticate,
    issue_token_pair,
    refresh_token_pair,
    register_user,
)
from app.services.auth_service import (
    logout as service_logout,
)

# Router mit Prefix + Tag. Prefix wird VOR jeden der unten deklarierten Pfade
# gesetzt: @router.post("/login") -> /api/v1/auth/login (wenn der Eltern-Router
# /api/v1 inkludiert — siehe api/v1/router.py).
# Empfehlung der FastAPI-Skill: Metadaten (prefix, tags) am Router, NICHT am
# include_router festlegen.
router = APIRouter(prefix="/auth", tags=["auth"])


# ----------------------------------------------------------------------------
# /auth/register — neuen User anlegen (öffentlich)
# ----------------------------------------------------------------------------
@router.post("/register", response_model=UserRead, status_code=status.HTTP_201_CREATED)
async def register(data: UserCreate, session: SessionDep):
    """
    Registriert einen neuen User.

    Eingabe (JSON): { email, password, full_name? }
    Antwort:        die neuen User-Daten (ohne Passwort).

    Bei belegter E-Mail: 409 Conflict.
    """
    try:
        user = await register_user(session, data)
    except EmailAlreadyExistsError:
        # Konflikt: die Ressource (E-Mail) existiert schon -> 409 (nicht 400).
        # `from None`: wir verbergen bewusst die interne Exception, weil die
        # Detail-Message generisch ist (keine Internas nach außen reichen).
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Diese E-Mail-Adresse ist bereits registriert.",
        ) from None
    return user


# ----------------------------------------------------------------------------
# /auth/login — Login mit JSON (für unser Frontend)
# ----------------------------------------------------------------------------
@router.post("/login", response_model=TokenPair)
async def login(data: UserLogin, session: SessionDep):
    """
    Login mit JSON-Body { email, password }.

    Antwort bei Erfolg: { access_token, refresh_token, token_type, user }.
    Antwort bei Misserfolg: 401.
    """
    try:
        user = await authenticate(session, data.email, data.password)
    except InvalidCredentialsError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Ungültige Anmeldedaten.",
            headers={"WWW-Authenticate": "Bearer"},
        ) from None
    return await issue_token_pair(session, user)


# ----------------------------------------------------------------------------
# /auth/login/oauth — Login mit FORM-DATA (für Swagger /docs)
# ----------------------------------------------------------------------------
@router.post("/login/oauth", response_model=TokenPair)
async def login_oauth(
    form_data: Annotated[OAuth2PasswordRequestForm, Depends()],
    session: SessionDep,
):
    """
    OAuth2-konformer Login via FORM-DATA.

    Genau genommen verlangt OAuth2 das Feld "username". Wir behandeln
    `username` einfach als E-Mail — für den User bedeutet das: im /docs-
    Authorize-Dialog einfach die E-Mail ins "username"-Feld eintragen.

    Warum FORM-DATA statt JSON? Weil das die OAuth2-Spezifikation so
    vorschreibt (RFC 6749). Der Authorize-Button im Swagger-UI schickt
    Form-Daten — und der Button funktioniert nur mit einem Endpunkt, der
    OAuth2PasswordRequestForm akzeptiert.
    """
    try:
        user = await authenticate(session, form_data.username, form_data.password)
    except InvalidCredentialsError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Ungültige Anmeldedaten.",
            headers={"WWW-Authenticate": "Bearer"},
        ) from None
    return await issue_token_pair(session, user)


# ----------------------------------------------------------------------------
# /auth/refresh — neues Token-Paar gegen den Refresh-Token
# ----------------------------------------------------------------------------
@router.post("/refresh", response_model=TokenPair)
async def refresh(data: RefreshIn, session: SessionDep):
    """
    Tauscht einen Refresh-Token gegen ein NEUES Token-Paar ein (Rotation).

    Eingabe: { refresh_token: "..." }
    Antwort: neues { access_token, refresh_token, user }.

    Schlägt fehl (ungültig/abgelaufen/revoked): 401.
    """
    try:
        return await refresh_token_pair(session, data.refresh_token)
    except InvalidCredentialsError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Refresh-Token ungültig oder abgelaufen. Bitte neu einloggen.",
        ) from None


# ----------------------------------------------------------------------------
# /auth/logout — Refresh-Token widerrufen
# ----------------------------------------------------------------------------
@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout_endpoint(request: Request, session: SessionDep):
    """
    Macht den Refresh-Token ungültig.

    Wir lesen das Token aus dem JSON-Body (loose). Im Frontend schicken wir
    beim Logout einfach { refresh_token: "..." } mit.
    """
    data = await request.json() if request.headers.get("content-type") == "application/json" else {}
    refresh_jwt = data.get("refresh_token")
    await service_logout(session, refresh_jwt)
    # 204 = No Content: Erfolg, aber kein Body.


# ----------------------------------------------------------------------------
# /auth/me — wer bin ich? (geschützt)
# ----------------------------------------------------------------------------
@router.get("/me", response_model=UserRead)
async def me(current_user: CurrentUserDep):
    """
    Gibt die Daten des aktuell eingeloggten Users zurück.

    Das ist eine GESCHÜTZTE Route: `CurrentUserDep` (siehe core/deps.py)
    führt automatisch die Authentifizierung durch. Schlägt diese fehl,
    kommt der Client gar nicht bis hierher, sondern bekommt direkt 401.
    """
    return current_user
