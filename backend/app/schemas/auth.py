"""
schemas/auth.py — Pydantic-Schemas für Auth-Eingaben/-Ausgaben
==============================================================

WARUM eigene Schemas, wenn wir doch SQLModel haben?
---------------------------------------------------
SQLModel vereint ORM + Pydantic, aber das bedeutet auch: das ORM-Modell
(`User` in models/) hat Felder wie `hashed_password`, `is_active`, `role`,
die NICHT nach draußen gehören. Würden wir das User-Modell direkt als
Antwort zurückgeben, würde (mit exclude=True nur teilweise helfen) leicht
ein Internal Field durchrutschen.

Saubere Trennung:
  - models/user.py        = DB-Zustand (was wird gespeichert?)
  - schemas/auth.py       = API-Zustand  (was geht rein/raus?)

Für jeden Anwendungsfall ein eigenes, minimal frohes Schema:
  - UserCreate  -> Eingabe beim Registrieren (email + password + optional name)
  - UserLogin   -> Eingabe beim Login (email + password)
  - UserRead    -> Ausgabe bei /me, /users  (ohne Passwort!)
  - Token       -> einzelnes JWT
  - TokenPair   -> Access + Refresh zusammen (was /login zurückgibt)
  - RefreshIn   -> Eingabe bei /refresh (nur der refresh_token-String)
"""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from app.models.user import UserRole


# ----------------------------------------------------------------------------
# Eingaben (vom Client geschickt)
# ----------------------------------------------------------------------------
class UserCreate(BaseModel):
    """Payload für POST /auth/register."""

    # EmailStr = Pydantic-Validierung auf echte E-Mail-Syntax.
    # (Erfordert das Paket "email-validator" — mit pydantic[email] oder
    #  email-validator installiert. Siehe pyproject.toml.)
    email: EmailStr
    # Min/Max Length als einfache Validierung. Strengere Passwort-Policy
    # (Groß/Klein/Ziffer/Zeichen) ließe sich mit Field(pattern=...) ergänzen.
    password: str = Field(min_length=8, max_length=128)
    full_name: str | None = Field(default=None, max_length=255)


class UserLogin(BaseModel):
    """Payload für POST /auth/login (JSON-Variante).

    ACHTUNG: Daneben unterstützen wir auch den Standard-OAuth2-Password-Flow
    mit Form-Daten (für den Authorize-Button in /docs). Siehe api/v1/auth.py.
    """

    email: EmailStr
    password: str


class RefreshIn(BaseModel):
    """Payload für POST /auth/refresh: der (alte) Refresh-Token."""

    refresh_token: str


# ----------------------------------------------------------------------------
# Ausgaben (vom Server zurückgegeben)
# ----------------------------------------------------------------------------
# `model_config = ConfigDict(from_attributes=True)` macht Folgendes:
# Erlaubt Pydantic, das Schema aus einem ORM-Objekt zu konstruieren
# (z. B. UserRead.model_validate(user_orm_instance)). Pydantic liest die
# Attribute per getattr — praktisch, wenn man ein ORM-Modell in der Response
# returned und FastAPI es automatisch konvertieren soll.
class UserRead(BaseModel):
    """Öffentliche User-Daten, wie sie von /me oder /users kommen.

    Enthält bewusst KEIN Passwort und keine internen Felder.
    """

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    email: EmailStr
    full_name: str | None
    role: UserRole
    is_active: bool
    created_at: datetime


class Token(BaseModel):
    """Ein einzelnes JWT, wie es im Authorization-Header geschickt wird."""

    access_token: str
    token_type: str = "bearer"


class TokenPair(BaseModel):
    """Was /login (und /refresh) zurückgeben: Access + Refresh zusammen.

    Der Client speichert den Refresh-Token, um bei abgelaufenem Access-Token
    einen neuen zu holen, ohne sich neu einloggen zu müssen.
    """

    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    # Wir geben die User-Daten gleich mit — so kann das Frontend sofort den
    # eingeloggten User anzeigen, ohne einen Extra-/me-Aufruf.
    user: UserRead
