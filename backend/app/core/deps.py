"""
core/deps.py — Auth-DEPENDENCIES (Authentifizierung + Autorisierung)
=====================================================================

Diese Datei ist der Dreh- und Angelpunkt für die Zugangskontrolle in FastAPI.

Konzept: FastAPI "Dependencies"
-------------------------------
Eine Dependency ist eine Funktion, die FastAPI VOR dem Endpunkt ausführt.
Das Ergebnis wird dem Endpunkt als Parameter übergeben. Klassische Beispiele:
  - DB-Session öffnen (siehe core/db.py: get_session)
  - User aus dem JWT laden (siehe unten: get_current_user)

Das Schöne: Dependencies kann man SCHACHTELN. `current_active_user` hängt von
`get_current_user` ab. `require_admin` hängt wiederum von `current_active_user`
ab. So baut man eine Pipeline, in der jeder Schritt eine Sache prüft.

────────────────────────────────────────────────────────────────────────────
AUTHENTIFIZIERUNG vs. AUTORISIERUNG (wichtig!)
────────────────────────────────────────────────────────────────────────────
  AUTHENTIFIZIERUNG (Authentication) = "Wer bist du?"
      -> E-Mail + Passwort prüfen, JWT ausstellen, JWT verifizieren.
      -> Ergebnis: ein bekannter User ist es. (Wer, ist geklärt.)

  AUTORISIERUNG (Authorization) = "Darfst du das?"
      -> Hat der User die nötige Rolle / Permission für DIESE Aktion?
      -> Ergebnis: 200 OK oder 403 Forbidden.

Beides ist nötig: Nur weil man authentifiziert ist (User bekannt), darf man
noch lange nicht alles. Ein normaler User darf nicht die User-Liste sehen.

In diesem Template:
  - get_current_user      -> Authentifizierung (wer bist du?)
  - current_active_user   -> dito + "ist dein Konto aktiv?"
  - require_admin         -> Autorisierung  (darfst du das? -> nur ADMIN)
"""

from typing import Annotated
from uuid import UUID

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.security import decode_token
from app.models.user import User, UserRole

# ----------------------------------------------------------------------------
# OAuth2-Schema: sagt FastAPI "erwarte einen Bearer-Token im Authorization-Header".
# ----------------------------------------------------------------------------
# tokenUrl ist der Endpunkt, an dem man Username/Passwort gegen ein Token tauscht.
# Im /docs-Swagger-UI erscheint deshalb der "Authorize"-Button, der gegen
# /api/v1/auth/login/oauth (Formular-Flow) geht — siehe api/v1/auth.py.
#
# auto_error=True (default): fehlt der Header, wirft die Dependency sofort 401.
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="api/v1/auth/login/oauth")


# ----------------------------------------------------------------------------
# 1) AUTHENTIFIZIERUNG: Token verifizieren -> User laden
# ----------------------------------------------------------------------------
async def get_current_user(
    token: Annotated[str, Depends(oauth2_scheme)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> User:
    """
    Liefert den zum Access-Token gehörenden User.

    Ablauf:
      1. `token` kommt automatisch vom oauth2_scheme (Authorization-Header).
         Fehlt der Header, hat FastAPI schon 401 geworfen.
      2. JWT dekodieren + Signatur prüfen.
      3. `sub` (User-ID) aus dem Payload lesen.
      4. User aus DB laden.
      5. Token-Typ muss "access" sein (ein Refresh-Token darf hier nicht funktionieren).

    Schlägt etwas fehl -> HTTP 401 mit WWW-Authenticate: Bearer (Standard-
    Antwort, die Browser/Clients als "neu einloggen" interpretieren).
    """
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Token konnte nicht validiert werden",
        headers={"WWW-Authenticate": "Bearer"},  # Standard für Bearer-Auth
    )

    # 2. JWT dekodieren. Jede Art von Fehler -> 401.
    try:
        payload = decode_token(token)
    except Exception:
        raise credentials_exception from None

    # 3. sub-Claim (User-ID) extrahieren.
    user_id_str = payload.get("sub")
    if user_id_str is None:
        raise credentials_exception

    # Extra-Check: nur Access-Tokens erlauben.
    if payload.get("type") != "access":
        raise credentials_exception

    # 4. User aus DB laden.
    try:
        user_id = UUID(user_id_str)
    except ValueError:
        raise credentials_exception from None

    user = await session.get(User, user_id)
    if user is None:
        raise credentials_exception

    return user


# ----------------------------------------------------------------------------
# 2) AUTHENTIFIZIERUNG + "Konto aktiv?"
# ----------------------------------------------------------------------------
async def get_current_active_user(
    current_user: Annotated[User, Depends(get_current_user)],
) -> User:
    """
    Wie get_current_user, ABER zusätzlich: ist das Konto aktiv?

    WICHTIG: Wir werfen 401 (NICHT 403), damit ein deaktivierter User nach
    außen nicht von einem "Token falsch"-Fall zu unterscheiden ist. Sonst
    würde ein Angreifer lernen: "Aha, diese E-Mail ist gesperrt".
    """
    if not current_user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token konnte nicht validiert werden",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return current_user


# ----------------------------------------------------------------------------
# 3) AUTORISIERUNG: nur Admins
# ----------------------------------------------------------------------------
async def get_current_admin_user(
    current_user: Annotated[User, Depends(get_current_active_user)],
) -> User:
    """
    AUTORISIERUNG: Nur ADMIN darf weiter.

    Authentifizierung (wer bist du?) war erfolgreich — sonst wären wir gar
    nicht hier. Jetzt kommt Autorisierung (darfst du das?):
      Ist die Rolle == ADMIN?

    Antwort 403 (Forbidden), nicht 401:
      401 = ich kenne dich nicht -> neu einloggen hilft.
      403 = ich kenne dich, ABER du darfst das nicht -> einloggen hilft nicht.
    """
    if current_user.role != UserRole.ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin-Rechte erforderlich",
        )
    return current_user


# ----------------------------------------------------------------------------
# Type-Aliase — das ist, was Endpunkte verwenden
# ----------------------------------------------------------------------------
# Sauberer, lesbarer Code im Endpunkt:
#
#   @router.get("/me")
#   async def me(current_user: CurrentUserDep): ...
#
#   @router.get("/users", dependencies=[Depends(RequireAdmin)])
#   async def list_users(...): ...
#
# (Empfehlung der FastAPI-Skill: immer Type-Aliase für Dependencies erstellen.)
CurrentUserDep = Annotated[User, Depends(get_current_active_user)]
# Für `dependencies=[...]` (ohne Parameter-Injection) nutzen wir die Funktion:
RequireAdmin = Depends(get_current_admin_user)
