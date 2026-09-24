"""
models/user.py — User-Modell (Tabelle "users")
==============================================

Das wichtigste Modell: Benutzerkonten mit Passwort, Rolle und Aktiv-Status.

WICHTIG: In SQLModel ist ein Modell gleichzeitig ORM-Tabelle UND Pydantic-
Schema. Das bedeutet: Standardmäßig werden ALLE Felder in API-Antworten
exponiert — inklusive `hashed_password`! Das wollen wir natürlich NICHT.

Deshalb definieren wir im schemas/-Ordner SEPARATE Pydantic-Modelle für
Ein-/Ausgabe (UserRead, UserCreate). Das User-Modell hier ist NUR für die DB.

So trennen wir sauber:
  - Modelle (hier)    = wie die Daten in der DB liegen
  - Schemas (schemas/) = wie die Daten in die API rein/rausgehen

Warum Rollen als Enum?
----------------------
Wir speichern die Rolle als Enum: "user" oder "admin". Das ist typsicher
(keine Tippfehler wie "adnin") und selbsterklärend. Für komplexere
Berechtigungen (fine-grained permissions) würde man zusätzlich eine
`user_permissions`-Tabelle anlegen — siehe README "Erweiterungen".
"""

import enum
import uuid
from typing import TYPE_CHECKING

import sqlalchemy as sa
from sqlalchemy import Column
from sqlmodel import Field, SQLModel

from app.models.base import TimestampMixin

# TYPE_CHECKING: Importe darunter nur für Type-Checker, nicht zur Laufzeit.
# Verhindert Zirkelimporte (RefreshToken referenziert User und umgekehrt).
if TYPE_CHECKING:
    pass


class UserRole(enum.StrEnum):
    """
    Mögliche Rollen eines Users.

    StrEnum (Python 3.11+) kombiniert str + Enum: die Werte sind
    JSON-serialisierbar UND typsicher (man kann nur USER/ADMIN zuweisen,
    keinen beliebigen String). Moderner Ersatz für das ältere
    `class X(str, enum.Enum)`-Muster.
    """

    USER = "user"
    ADMIN = "admin"


class User(TimestampMixin, SQLModel, table=True):
    """
    Tabelle "users" — ein Benutzerkonto.

    Vererbung:
      TimestampMixin -> liefert created_at / updated_at
      SQLModel       -> macht es zur ORM-Tabelle (wegen table=True)
    """

    __tablename__ = "users"

    # Primärschlüssel. Wir nutzen UUID statt AutoIncrement-Integer, weil UUIDs:
    #   * keine Aufschluss über die Anzahl der User geben (Sicherheit)
    #   * ohne zentralen Counter erzeugt werden können (verteilt, ID-Kollision sicher)
    #   * perfekt für öffentliche IDs geeignet sind
    # default_factory=uuid.uuid4 -> Python erzeugt eine UUID beim Anlegen.
    id: uuid.UUID = Field(
        default_factory=uuid.uuid4,
        primary_key=True,
        index=True,
        nullable=False,
    )

    # E-Mail = eindeutig (jeder E-Mail darf nur ein Konto haben).
    # index=True -> DB legt einen Index an, beschleunigt die Login-Suche.
    email: str = Field(
        unique=True,
        index=True,
        nullable=False,
        max_length=255,
        # schema_extra im Field: Validierung für Pydantic (z. B. Format-E-Mail).
        # In SQLModel kann man Pydantic-Validatoren direkt im Field setzen.
    )

    # Gehashtes Passwort. NIEMALS das Klartext-Passwort speichern!
    # Das Hashen passiert in app/core/security.py via pwdlib.
    # Feldname "hashed_password" statt "password" dokumentiert das explizit.
    hashed_password: str = Field(nullable=False, exclude=True)
    # ^ exclude=True -> wird bei SQLModel.dict()/JSON nie serialisiert.
    #   Doppelter Schutz: Auch wenn jemand versehentlich das User-Model returned,
    #   landet das Passwort nicht in der Antwort.

    # Vollständiger Name (optional, nur für Anzeige).
    full_name: str | None = Field(default=None, max_length=255)

    # Rolle: USER (default) oder ADMIN. Wir speichern sie in der DB als
    # Postgres-Enum-Typ (siehe alembic/versions/0001_initial.py -> "userrole").
    #
    # sa_column=Column(sa.Enum(...)) statt nur `Field(...)` sagt SQLModel: "Nutze
    # diesen konkreten SQLAlchemy-Spaltentyp." Zwei wichtige Flags am Enum:
    #   * values_callable=lambda e: [m.value for m in e]:
    #     SQLAlchemy nimmt standardmäßig die Enum-NAMEN ("ADMIN"), wir wollen
    #     aber die Enum-WERTE ("admin") — sonst passt es nicht zum DB-Typ.
    #   * create_type=False: der Typ wird durch Alembic-Migrationen angelegt,
    #     SQLAlchemy soll ihn NICHT zur Laufzeit nochmal automatisch erzeugen.
    role: UserRole = Field(
        default=UserRole.USER,
        sa_column=Column(
            sa.Enum(
                UserRole,
                name="userrole",
                create_type=False,
                values_callable=lambda e: [m.value for m in e],
            ),
            nullable=False,
            server_default=sa.text("'user'"),
        ),
    )

    # Aktiv-Status. False = Konto gesperrt (z. B. nach Kündigung), kann sich
    # nicht einloggen. Unterschied zu "deleted": deaktivierte User bleiben
    # erhalten (Referenzen auf sie bleiben gültig), können sich nur nicht anmelden.
    is_active: bool = Field(default=True, nullable=False)

    # Beziehung zu Refresh-Tokens (OneToMany).
    # `Relationship` wird lazy geladen; wir vermeiden das im async-Kontext
    # und holen Tokens stattdessen explizit per select(). Dafür gibt es hier
    # kein Relationship-Feld — das ist mit async sauberer.
    # (Wer's mag: from sqlmodel import Relationship und dann
    #  tokens: list["RefreshToken"] = Relationship(back_populates="user"))


# Hilfsfunktion für Type-Checker (verkettete Imports):
if TYPE_CHECKING:
    # RefreshToken referenziert User via ForeignKey — siehe refresh_token.py.
    pass
