"""
core/security.py — Hashing + JWT (die KRYPTO-Primitiven)
=========================================================

Hier befindet sich die KRYPTO-Logik: Passwort-Hashing und JWT.

Zwei Themen, die man verwechseln kann — deshalb eine kleine Einführung:

────────────────────────────────────────────────────────────────────────────
A) PASSWORT-HASHING (pwdlib / Argon2 / bcrypt)
────────────────────────────────────────────────────────────────────────────
Passwörter dürfen NIEMALS im Klartext gespeichert werden — nicht mal
verschlüsselt (verschlüsselt = umkehrbar). Stattdessen HASEHN wir sie:

  hash("secret")  -> "$argon2id$v=19$m=..."  (Einweg, nicht umkehrbar)

Beim Login hashen wir das eingegebene Passwort und vergleichen die Hashes.
Stimmen sie überein, war das Passwort korrekt. So kann auch ein DB-Leak die
Passwörter nicht preisgeben.

Was macht ein GUTER Hash-Algorithmus?
  1. "Salt": Jedes Passwort bekommt einen Zufalls-Beimischung. So haben zwei
     User mit dem gleichen Passwort ("12345678") unterschiedliche Hashes.
  2. "Slow": Argon2/bcrypt sind ABSICHTLICH langsam (mehrere 100 ms pro Hash).
     Das macht Brute-Force-Attacken unpraktisch (MD5/SHA sind zu schnell!).
  3. "Memory-hard": Argon2 braucht viel RAM, erschwert GPU-Attacken.

`pwdlib` (modern) kümmert sich um all das für uns; wir müssen nur
PasswordHash.recommended() aufrufen.

────────────────────────────────────────────────────────────────────────────
B) JWT (JSON Web Token)
────────────────────────────────────────────────────────────────────────────
Ein JWT ist ein signiertes "Token" — drei Base64-Teile, mit Punkt getrennt:

   HEADER.PAYLOAD.SIGNATURE

  * HEADER:  Algorithmus ("alg"), z. B. {"alg":"HS256","typ":"JWT"}.
  * PAYLOAD: Die eigentlichen Daten (claims), z. B.:
               {"sub": "user-uuid", "exp": 1234567890, "type": "access"}
             WICHTIG: Der Payload ist nur Base64 — NICHT verschlüsselt!
             Packe NIEMALS Passwörter/Secrets hinein.
  * SIGNATURE: HMAC-SHA256( HEADER + PAYLOAD, SECRET_KEY )
             Der Server kann die Signatur verifizieren — nur wer den
             SECRET_KEY hat, kann gültige Token erzeugen.

Weil die Signatur den Payload abdeckt, kann der Token-Inhalt nicht gefälscht
werden (ohne Schlüssel). Deshalb ist JWT "stateless" authentifizierbar:
der Server muss nicht pro Request die Session-DB fragen — Signatur ok reicht.

HS256 vs. RS256:
  * HS256 = symmetrisch (derselbe SECRET_KEY signiert + verifiziert). Simple,
    gut für Monolithen. Das nutzen wir hier.
  * RS256 = asymmetrisch (privater Schlüssel signiert, öffentlicher verifiziert).
    Praktisch, wenn ein ANDERER Service Token nur verifizieren soll (ohne
    sie selbst erzeugen zu dürfen). Für Microservices.
"""

from datetime import UTC, datetime, timedelta
from typing import Any, Literal
from uuid import UUID

import jwt
from pwdlib import PasswordHash

from app.core.config import settings

# ----------------------------------------------------------------------------
# Password-Hashing (pwdlib)
# ----------------------------------------------------------------------------
# PasswordHash.recommended() wählt den besten verfügbaren Algorithmus (Argon2
# oder bcrypt) automatisch. Ein global erzeugter Hasher reicht für die ganze App.
password_hash = PasswordHash.recommended()


def hash_password(plain_password: str) -> str:
    """
    Hasht ein Klartext-Passwort.

    Rückgabe ist ein String wie "$argon2id$v=19$m=..." — dieser enthält
    SALT + Hash + Parameter in einem. Beim Verifizieren braucht pwdlib nur
    diesen String + das eingegebene Passwort.
    """
    return password_hash.hash(plain_password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """
    Vergleicht ein Klartext-Passwort mit dem gespeicherten Hash.

    WICHTIG: `verify` ist "constant-time" implementiert, d. h. die Laufzeit
    verät NICHT, an welcher Stelle die Passwörter abweichen. Sonst könnte man
    per Zeitmessung (Timing-Attack) Informationen über das Passwort lernen.
    """
    return password_hash.verify(plain_password, hashed_password)


# ----------------------------------------------------------------------------
# JWT-Erzeugung & -Verifikation
# ----------------------------------------------------------------------------
# Wir definieren zwei Token-Typen:
#   "access"  -> kurzer Token für jeden API-Aufruf
#                (s. settings.access_token_expire_minutes).
#   "refresh" -> langer Token, nur für den /refresh-Endpunkt
#                (s. settings.refresh_token_expire_days).
# Im Payload tragen wir `type` ein, damit ein Access-Token nicht als Refresh
# (und umgekehrt) benutzt werden kann.

TokenType = Literal["access", "refresh"]


def create_token(
    *,
    subject: str | UUID,
    token_type: TokenType,
    expires_delta: timedelta,
    extra_claims: dict[str, Any] | None = None,
) -> str:
    """
    Erzeugt ein signiertes JWT.

    Parameter:
      subject:        der "sub"-Claim. Bei uns die User-ID.
      token_type:     "access" oder "refresh" (wird im Payload eingetragen).
      expires_delta:  Lebensdauer (timedelta). Abgelaufene Token gelten nicht.
      extra_claims:   weitere Claims (z. B. {"jti": <uuid>} für Refresh-Tokens).

    Rückgabe: das JWT als String ("xxx.yyy.zzz").
    """
    # Zeitpunkt in UTC als UNIX-Timestamp. PyJWT versteht auch datetime-Objekte
    # direkt, aber Timestamps sind portabler.
    now = datetime.now(UTC)
    expire = now + expires_delta

    # Die "Claims" = der Payload-Inhalt.
    to_encode: dict[str, Any] = {
        # "sub" (subject) = WEM gehört das Token? Konvention aus RFC 7519.
        # Als STRING serialisieren (UUID ist sonst nicht JSON-kompatibel).
        "sub": str(subject),
        "type": token_type,
        # "iat" = issued at (wann erzeugt)
        "iat": now,
        # "exp" = expiration (wann ungültig). PyJWT prüft das AUTOMATISCH
        # beim decode — abgelaufene Token werfen ExpiredSignatureError.
        "exp": expire,
    }
    if extra_claims:
        to_encode.update(extra_claims)

    # encode = signieren + Base64-kodieren. algorithms MUSS eine Liste sein
    # (Sicherheits-Feature: PyJWT verweigert decode ohne explizite Algorithmus-
    # Liste, um "Algorithm-Confusion"-Attacken zu verhindern).
    return jwt.encode(to_encode, settings.secret_key, algorithm=settings.jwt_algorithm)


def decode_token(token: str) -> dict[str, Any]:
    """
    Dekodiert + verifiziert ein JWT.

    Schlägt fehl (signatur falsch / abgelaufen / anderer Algorithmus), wirft
    PyJWT eine `jwt.InvalidTokenError`-Exception — die ruft der Aufrufer auf
    und macht daraus einen HTTP 401 (siehe app/core/deps.py).
    """
    return jwt.decode(token, settings.secret_key, algorithms=[settings.jwt_algorithm])
