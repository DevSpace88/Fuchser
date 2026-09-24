"""
models/__init__.py — zentrale Import-Sammelstelle für alle Tabellen-Modelle.

WARUM das wichtig ist:
  SQLModel.metadata (bzw. SQLAlchemy) sammelt alle @table-Modelle automatisch
  ein, SOBALD die Klasse importiert wurde. Damit Alembic & create_all alle
  Tabellen kennen, reicht ein `from app.models import *` von überall.

  Ohne diesen Sammelimport müsste man an 10 Stellen einzeln importieren
  und würde leicht eine Tabelle vergessen -> Migration fehlt sie.
"""

from app.models.base import TimestampMixin
from app.models.conversation import Conversation
from app.models.document import Document
from app.models.refresh_token import RefreshToken
from app.models.research_project import ResearchProject, ResearchStatus
from app.models.user import User, UserRole

__all__ = [
    "TimestampMixin",
    "Conversation",
    "Document",
    "RefreshToken",
    "ResearchProject",
    "ResearchStatus",
    "User",
    "UserRole",
]
