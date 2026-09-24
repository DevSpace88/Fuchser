"""
core/security.py — Hashing + JWT (the CRYPTO primitives)
=========================================================

This is where the CRYPTO logic lives: password hashing and JWT.

Two topics that are easy to confuse — hence a short introduction:

────────────────────────────────────────────────────────────────────────────
A) PASSWORD HASHING (pwdlib / Argon2 / bcrypt)
────────────────────────────────────────────────────────────────────────────
Passwords must NEVER be stored in plain text — not even encrypted
(encrypted = reversible). Instead we HASH them:

  hash("secret")  -> "$argon2id$v=19$m=..."  (one-way, not reversible)

At login we hash the entered password and compare the hashes.
If they match, the password was correct. That way even a DB leak cannot
expose the passwords.

What makes a GOOD hashing algorithm?
  1. "Salt": every password gets a random admixture. That way two users
     with the same password ("12345678") get different hashes.
  2. "Slow": Argon2/bcrypt are DELIBERATELY slow (several 100 ms per hash).
     That makes brute-force attacks impractical (MD5/SHA are too fast!).
  3. "Memory-hard": Argon2 needs lots of RAM, which hampers GPU attacks.

`pwdlib` (modern) takes care of all of this for us; we only need to
call PasswordHash.recommended().

────────────────────────────────────────────────────────────────────────────
B) JWT (JSON Web Token)
────────────────────────────────────────────────────────────────────────────
A JWT is a signed "token" — three Base64 parts, separated by dots:

   HEADER.PAYLOAD.SIGNATURE

  * HEADER:  algorithm ("alg"), e.g. {"alg":"HS256","typ":"JWT"}.
  * PAYLOAD: the actual data (claims), e.g.:
               {"sub": "user-uuid", "exp": 1234567890, "type": "access"}
             IMPORTANT: the payload is only Base64 — NOT encrypted!
             NEVER put passwords/secrets in there.
  * SIGNATURE: HMAC-SHA256( HEADER + PAYLOAD, SECRET_KEY )
             The server can verify the signature — only whoever has the
             SECRET_KEY can produce valid tokens.

Because the signature covers the payload, the token content cannot be
forged (without the key). That is why a JWT can be verified "statelessly":
the server does not need to query a session DB per request — a valid
signature is enough.

HS256 vs. RS256:
  * HS256 = symmetric (the same SECRET_KEY signs + verifies). Simple,
    good for monoliths. That is what we use here.
  * RS256 = asymmetric (a private key signs, a public one verifies).
    Practical when ANOTHER service is only supposed to verify tokens
    (without being allowed to create them itself). For microservices.
"""

from datetime import UTC, datetime, timedelta
from typing import Any, Literal
from uuid import UUID

import jwt
from pwdlib import PasswordHash

from app.core.config import settings

# ----------------------------------------------------------------------------
# Password hashing (pwdlib)
# ----------------------------------------------------------------------------
# PasswordHash.recommended() automatically picks the best available algorithm
# (Argon2 or bcrypt). One globally created hasher is enough for the whole app.
password_hash = PasswordHash.recommended()


def hash_password(plain_password: str) -> str:
    """
    Hashes a plain-text password.

    The return value is a string like "$argon2id$v=19$m=..." — it contains
    SALT + hash + parameters in one. For verification, pwdlib only needs
    this string + the entered password.
    """
    return password_hash.hash(plain_password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """
    Compares a plain-text password with the stored hash.

    IMPORTANT: `verify` is implemented "constant-time", i.e. the runtime
    does NOT reveal at which position the passwords differ. Otherwise one
    could learn information about the password by measuring time (timing
    attack).
    """
    return password_hash.verify(plain_password, hashed_password)


# ----------------------------------------------------------------------------
# JWT creation & verification
# ----------------------------------------------------------------------------
# We define two token types:
#   "access"  -> short token for every API call
#                (see settings.access_token_expire_minutes).
#   "refresh" -> long token, only for the /refresh endpoint
#                (see settings.refresh_token_expire_days).
# We record `type` in the payload so that an access token cannot be used as
# a refresh token (and vice versa).

TokenType = Literal["access", "refresh"]


def create_token(
    *,
    subject: str | UUID,
    token_type: TokenType,
    expires_delta: timedelta,
    extra_claims: dict[str, Any] | None = None,
) -> str:
    """
    Creates a signed JWT.

    Parameters:
      subject:        the "sub" claim. For us, the user ID.
      token_type:     "access" or "refresh" (recorded in the payload).
      expires_delta:  lifetime (timedelta). Expired tokens are invalid.
      extra_claims:   additional claims (e.g. {"jti": <uuid>} for refresh tokens).

    Returns: the JWT as a string ("xxx.yyy.zzz").
    """
    # Point in time in UTC as a UNIX timestamp. PyJWT also understands datetime
    # objects directly, but timestamps are more portable.
    now = datetime.now(UTC)
    expire = now + expires_delta

    # The "claims" = the payload content.
    to_encode: dict[str, Any] = {
        # "sub" (subject) = WHOM does the token belong to? Convention from RFC 7519.
        # Serialize as a STRING (otherwise a UUID is not JSON-compatible).
        "sub": str(subject),
        "type": token_type,
        # "iat" = issued at (when it was created)
        "iat": now,
        # "exp" = expiration (when it becomes invalid). PyJWT checks this
        # AUTOMATICALLY on decode — expired tokens raise ExpiredSignatureError.
        "exp": expire,
    }
    if extra_claims:
        to_encode.update(extra_claims)

    # encode = sign + Base64-encode. algorithms MUST be a list
    # (security feature: PyJWT refuses to decode without an explicit algorithm
    # list, to prevent "algorithm confusion" attacks).
    return jwt.encode(to_encode, settings.secret_key, algorithm=settings.jwt_algorithm)


def decode_token(token: str) -> dict[str, Any]:
    """
    Decodes + verifies a JWT.

    On failure (wrong signature / expired / different algorithm), PyJWT
    raises a `jwt.InvalidTokenError` exception — the caller catches it
    and turns it into an HTTP 401 (see app/core/deps.py).
    """
    return jwt.decode(token, settings.secret_key, algorithms=[settings.jwt_algorithm])
