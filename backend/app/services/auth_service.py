"""
services/auth_service.py — GESCHÄFTSLOGIK für Auth
==================================================

Hier liegt die eigentliche Auth-Logik: Registrieren, Login, Token-Erzeugung,
Refresh (mit Rotation), Logout.

Warum eine Service-Schicht?
---------------------------
Die Endpunkte (api/v1/auth.py) sollen DÜNN sein: HTTP-Sachen (Statuscodes,
Request-Parsing) und Delegation. Die eigentliche Logik gehört in einen
Service. Vorteile:
  * Testbar ohne HTTP-Layer (Unit-Tests rufen Funktionen direkt auf).
  * Wiederverwendbar (z. B. könnte ein CLI-Command die gleiche Logik nutzen).
  * Übersichtlicher: ein Endpunkt ist 5 Zeilen statt 50.

────────────────────────────────────────────────────────────────────────────
DER REFRESH-FLOW (Rotation) — der schwierigste Teil
────────────────────────────────────────────────────────────────────────────
1. Login:     Client bekommt (access, refresh_a). Wir speichern refresh_a
              (gehasht) mit revoked=False in der DB.
2. Access läuft ab. Client schickt refresh_a an /refresh.
3. Server:
     a. Verifiziert Signatur von refresh_a (PyJWT).
     b. Sucht den zugehörigen DB-Eintrag via jti (=refresh_token.id).
     c. Prüft: revoked=False? expires_at in Zukunft? user noch aktiv?
     d. Macht refresh_a revoked=True ("Rotation": der alte ist tot).
     e. Stellt NEUES Paar (access, refresh_b) aus und speichert refresh_b.
4. Ab jetzt gilt NUR noch refresh_b. Würde ein Angreifer refresh_a stehlen
   und einsetzen, wird der legitime Client beim nächsten /refresh merken,
   dass sein Token revoked ist -> Alarm ("token reuse detection").

Vereinfachung in diesem Template:
  Wir verzichten auf die erweiterte "Reuse Detection" (bei reuse ALLE Tokens
  des Users sperren). Das ist eine Härtung, die für Lernzwecke optional ist —
  im README unter "Erweiterungen" dokumentiert.
"""

from datetime import UTC, timedelta
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.core.config import settings
from app.core.security import create_token, decode_token, hash_password, verify_password
from app.models.refresh_token import RefreshToken
from app.models.user import User, UserRole
from app.schemas.auth import TokenPair, UserCreate, UserRead


# Duplicate-Email-Exception: wir definieren sie als eigene Exception, damit der
# Endpunkt sie gezielt fangen und in einen sauberen 409 übersetzen kann.
class EmailAlreadyExistsError(Exception):
    """Es gibt bereits einen User mit dieser E-Mail."""


class InvalidCredentialsError(Exception):
    """Login/Refresh fehlgeschlagen (falsche Daten, abgelaufen, revoked)."""


# ============================================================================
# 1) REGISTRIEREN
# ============================================================================
async def register_user(session: AsyncSession, data: UserCreate) -> User:
    """
    Legt einen neuen User an.

    Ablauf:
      1. Prüfen, ob es die E-Mail schon gibt -> falls ja, Fehler.
      2. Passwort hashen (NIEMALS Klartext speichern!).
      3. User-Objekt bauen und in der DB speichern.

    Neue User sind per Default Rolle=USER und is_active=True.
    (Den ersten Admin legen wir via SEED_ADMIN_* beim Start an — siehe lifespan.)
    """
    # SELECT * FROM users WHERE email = ?  — asynchron ausgeführt.
    existing = await session.exec(select(User).where(User.email == data.email))
    if existing.first() is not None:
        raise EmailAlreadyExistsError(data.email)

    user = User(
        email=data.email,
        hashed_password=hash_password(data.password),
        full_name=data.full_name,
        role=UserRole.USER,
        is_active=True,
    )
    session.add(user)
    await session.commit()
    # refresh() lädt die von der DB erzeugten Felder (id, created_at, ...) nach.
    await session.refresh(user)
    return user


# ============================================================================
# 2) LOGIN (Authentifizieren + Token-Paar ausstellen)
# ============================================================================
async def authenticate(session: AsyncSession, email: str, password: str) -> User:
    """
    Prüft E-Mail + Passwort. Gibt den User zurück, falls korrekt.
    Schlägt fehl -> InvalidCredentialsError.

    WICHTIG (Sicherheit): Bei falschem Login verraten wir NICHT, OB die E-Mail
    existiert — die Fehlermeldung ist immer dieselbe ("Ungültige Anmeldedaten").
    Sonst könnte ein Angreifer durch Ausprobieren herausfinden, welche E-Mails
    registriert sind (User-Enumeration).
    """
    user = (await session.exec(select(User).where(User.email == email))).first()
    # Vorsicht: user kann None sein. Wir rufen verify_password dann nicht auf,
    # sondern schmeißen direkt — aber wir tun das so, dass die Antwort identisch
    # mit dem "Passwort falsch"-Fall ist.
    if user is None or not verify_password(password, user.hashed_password):
        raise InvalidCredentialsError()
    if not user.is_active:
        raise InvalidCredentialsError()
    return user


async def issue_token_pair(session: AsyncSession, user: User) -> TokenPair:
    """
    Stellt ein (access, refresh)-Paar aus und speichert den Refresh-Token.

    Wird sowohl beim Login als auch beim Refresh aufgerufen (DRY).
    """
    # --- Access-Token: kurz, enthält NUR die User-ID und den Typ. ---
    access = create_token(
        subject=user.id,
        token_type="access",
        expires_delta=timedelta(minutes=settings.access_token_expire_minutes),
    )

    # --- Refresh-Token: lang. Wir erzeugen erst die Token-ID (jti), dann
    #     das JWT, hashen es für die DB und legen eine Zeile an. ---
    refresh_record = RefreshToken(
        user_id=user.id,
        # Hash wird unten gesetzt, sobald wir das JWT haben.
        token_hash="",  # Platzhalter, wird gleich überschrieben
        expires_at=_refresh_expiry(),
    )
    # refresh_record.id wurde von default_factory=uuid.uuid4 schon belegt.
    refresh_jwt = create_token(
        subject=user.id,
        token_type="refresh",
        expires_delta=timedelta(days=settings.refresh_token_expire_days),
        extra_claims={"jti": str(refresh_record.id)},
    )
    # Hash DES JWT-Strings speichern, nicht das JWT selbst.
    refresh_record.token_hash = hash_password(refresh_jwt)

    session.add(refresh_record)
    await session.commit()

    return TokenPair(
        access_token=access,
        refresh_token=refresh_jwt,
        user=UserRead.model_validate(user),
    )


def _refresh_expiry():
    """Hilfsfunktion: Ablaufzeitpunkt des Refresh-Tokens als datetime."""
    from datetime import datetime
    from datetime import timedelta as _td

    return datetime.now(UTC) + _td(days=settings.refresh_token_expire_days)


# ============================================================================
# 3) REFRESH (neues Paar + Rotation des alten)
# ============================================================================
async def refresh_token_pair(session: AsyncSession, refresh_jwt: str) -> TokenPair:
    """
    Tauscht einen Refresh-Token gegen ein neues Paar ein (Rotation).

    Schritte siehe Modul-Docstring (DER REFRESH-FLOW).
    """
    # --- 1. JWT dekodieren (prüft Signatur + Ablauf) ---
    try:
        payload = decode_token(refresh_jwt)
    except Exception as e:  # jwt.InvalidTokenError + Unterklassen
        raise InvalidCredentialsError() from e

    # --- 2. Typ prüfen: ein Access-Token darf hier NICHT funktionieren ---
    if payload.get("type") != "refresh":
        raise InvalidCredentialsError()

    # --- 3. jti (Token-ID) extrahieren und DB-Eintrag laden ---
    jti = payload.get("jti")
    if not jti:
        raise InvalidCredentialsError()
    record = await session.get(RefreshToken, UUID(jti))
    if record is None:
        raise InvalidCredentialsError()

    # --- 4. Hash vergleichen (Token gehört zu dieser jti?) ---
    # Auch bei gleicher jti: Stimmt der Hash? (Verteidigung, falls jti leaked
    # aber das echte JWT nicht.)
    if not verify_password(refresh_jwt, record.token_hash):
        raise InvalidCredentialsError()

    # --- 5. Status prüfen: revoked? abgelaufen? user aktiv? ---
    if record.revoked:
        raise InvalidCredentialsError()
    # expires_at wird beim decode bereits geprüft (exp-Claim). Wir prüfen
    # zusätzlich den DB-Wert, falls jemand von Hand in der DB herumspielt.
    #
    # Robustheit: Einige DBs (z. B. SQLite) speichern zeitzone-bewusste
    # Datetimes als "naive" Werte zurück. Wir normalisieren beide Seiten auf
    # UTC, sodass der Vergleich nie mit "offset-naive vs offset-aware" crasht.
    from datetime import datetime

    def _to_aware_utc(dt: datetime) -> datetime:
        if dt.tzinfo is None:
            return dt.replace(tzinfo=UTC)
        return dt.astimezone(UTC)

    if _to_aware_utc(record.expires_at) < datetime.now(UTC):
        raise InvalidCredentialsError()

    user = await session.get(User, record.user_id)
    if user is None or not user.is_active:
        raise InvalidCredentialsError()

    # --- 6. ROTATION: alten Token widerrufen ---
    record.revoked = True
    session.add(record)
    await session.commit()

    # --- 7. Neues Paar ausstellen ---
    return await issue_token_pair(session, user)


# ============================================================================
# 4) LOGOUT (Refresh-Token widerrufen)
# ============================================================================
async def logout(session: AsyncSession, refresh_jwt: str | None) -> None:
    """
    Macht den Refresh-Token ungültig (revoked=True).

    Access-Tokens können nicht widerrufen werden (stateless!). Sie laufen
    aber automatisch nach access_token_expire_minutes ab — deshalb sind sie
    ja so kurz. Refresh-Tokens sind lang, deshalb ist Logout wichtig.
    """
    if not refresh_jwt:
        return  # nichts zu tun

    try:
        payload = decode_token(refresh_jwt)
    except Exception:
        return  # ungültiges Token -> stillschweigend ignorieren (idempotent)

    if payload.get("type") != "refresh":
        return

    jti = payload.get("jti")
    if not jti:
        return
    record = await session.get(RefreshToken, UUID(jti))
    if record is not None and not record.revoked:
        record.revoked = True
        session.add(record)
        await session.commit()
