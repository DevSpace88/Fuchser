"""
models/base.py — Gemeinsame Basis für alle Tabellen-Modelle
===========================================================

Hier definieren wir ein `TimestampMixin`, das JEDE Tabelle automatisch um
zwei Spalten ergänzt:
  - created_at: wann wurde der Datensatz angelegt?
  - updated_at: wann wurde er zuletzt geändert?

So muss nicht jedes Model diese Felder wiederholen — DRY (Don't Repeat Yourself).

Konzept SQLModel
----------------
SQLModel (von FastAPI-Autor Sebastián Ramirez) verschmilzt ZWEI Welten:
  * Pydantic  -> Datenvalidierung + Serialisierung (für die API)
  * SQLAlchemy -> ORM (für die Datenbank)

Ein einziges `class User(SQLModel, table=True)` ersetzt also das sonst
getrennte ORM-Modell (SQLAlchemy) und das Schema (Pydantic).

`table=True` aktiviert den ORM-Modus. Ohne `table=True` ist es ein reines
Pydantic-Modell (nützlich für Response/Input-Schemas, siehe schemas/).
"""

from datetime import UTC, datetime

from sqlalchemy import DateTime, func
from sqlmodel import Field, SQLModel


class TimestampMixin(SQLModel):
    """
    Mixin, das created_at / updated_at hinzufügt.

    Ein "Mixin" ist eine Klasse, die man als Basis anderen Klassen mitgibt,
    um gemeinsam genutzte Felder/Methoden an einer Stelle zu haben.

    WICHTIG: Diese Klasse hat NICHT `table=True`. Sie ist eine "Basis", die
    von echten Tabellen-Modellen beerbt wird. Mixin-Modelle ohne table=True
    werden NICHT als eigene Tabelle angelegt — nur die Felder werden
    weitervererbt.
    """

    # sa_column_kwargs + sa_type statt sa_column=Column(...):
    #
    # ACHTUNG FALLSTRICK: Ein `sa_column=Column(...)` im Mixin erzeugt EINE
    # konkrete Column-Instanz, die an EINE Tabelle gebunden wird. Sobald ein
    # ZWEITES Modell das Mixin erbt, crasht SQLAlchemy ("Column already
    # assigned"). sa_column_kwargs/sa_type sind dagegen nur KONFIGURATION —
    # SQLModel baut daraus pro Modell eine frische Column. Gleiches DDL,
    # aber mixin-tauglich.
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        sa_type=DateTime(timezone=True),
        sa_column_kwargs={
            "server_default": func.now(),
            "nullable": False,
        },
    )
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        sa_type=DateTime(timezone=True),
        sa_column_kwargs={
            "server_default": func.now(),
            "onupdate": func.now(),
            "nullable": False,
        },
    )
