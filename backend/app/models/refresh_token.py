"""
models/refresh_token.py — Refresh-Token-Tabelle
===============================================

Warum speichern wir Refresh-Tokens in der DB?
---------------------------------------------
Ein Refresh-Token ist lange gültig (z. B. 7 Tage). Wenn er gestohlen wird,
könnte ein Angreifer 7 Tage lang neue Access-Tokens erzeugen — schlimm.

Deshalb speichern wir Refresh-Tokens serverseitig und können so:
  1. Bei Logout den Token gezielt widerrufen (revoked=True).
  2. Bei Rotation (neuer Token bei /refresh) den alten widerrufen.
  3. Bei Verdacht ALLE Tokens eines Users auf einmal sperren.

Gehasht statt Klartext:
Wir speichern nicht das JWT selbst, sondern den HASH des Tokens. So nützt
ein DB-Leak den Angreifer nichts (die Hashes können nicht als Token verwendet
werden). Same Pattern wie bei Passwörtern.

Die Token-ID im JWT (`jti` = "JWT ID") ist die UUID, unter der wir den Token
in der DB finden. Sie ist im JWT enthalten, aber der Lookup passiert nur
serverseitig über die gehashte Form.
"""

import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import Column, DateTime, ForeignKey
from sqlmodel import Field, SQLModel

if TYPE_CHECKING:
    # Nur für Type-Checker; verhindert Zirkelimporte zur Laufzeit.
    pass


class RefreshToken(SQLModel, table=True):
    """
    Tabelle "refresh_tokens" — eine Zeile pro ausgegebenem Refresh-Token.

    Lebenszyklus:
      1. Beim Login wird eine Zeile angelegt (revoked=False, expires_at in Zukunft).
      2. Bei /refresh wird die alte revoked=True und eine NEUE angelegt ("Rotation").
      3. Bei Logout wird die Zeile revoked=True.
      4. Abgelaufene oder widerrufene Tokens sind ungültig.
    """

    __tablename__ = "refresh_tokens"

    # Primärschlüssel. Gleichzeitig ist das die "jti" (JWT-ID), die im Token-
    # Payload steht. Beim Verifizieren schauen wir anhand dieser ID in der DB nach.
    id: uuid.UUID = Field(
        default_factory=uuid.uuid4,
        primary_key=True,
        index=True,
        nullable=False,
    )

    # Fremdschlüssel auf users.id. `ondelete="CASCADE"` bedeutet: Wenn der User
    # gelöscht wird, werden auch seine Tokens gelöscht (keine Waisen).
    user_id: uuid.UUID = Field(
        sa_column=Column(
            ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
    )

    # HASH des Tokens (nicht der Token selbst!). Siehe Sicherheits-Kommentar oben.
    # Wir hashen mit der gleichen pwdlib-Funktion wie Passwörter (Argon2/bcrypt).
    # Beim Verifizieren hashen wir das eingehende Token und vergleichen.
    token_hash: str = Field(nullable=False, unique=True, index=True)

    # Wann läuft der Token ab? After this date, refresh fails (Client muss neu login).
    # sa_column mit DateTime(timezone=True): Postgres speichert TIMESTAMPTZ,
    # asyncpg/psycopg erwarten dann aware datetimes — konsistent über die ganze
    # Pipeline (siehe auch _refresh_expiry() im auth_service, das aware UTC liefert).
    expires_at: datetime = Field(
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )

    # Widerrufen? True = Token ist ungültig (Logout, Rotation, manuelle Sperrung).
    revoked: bool = Field(default=False, nullable=False)

    # Wann wurde er ausgestellt? (Für Audit-Zwecke.)
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
