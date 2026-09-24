"""
models/__init__.py — central import hub for all table models.

WHY this matters:
  SQLModel.metadata (i.e. SQLAlchemy) collects all @table models automatically
  AS SOON AS the class has been imported. For Alembic & create_all to know all
  tables, a single `from app.models import *` from anywhere is enough.

  Without this hub import you would have to import each model individually in
  10 places and could easily forget a table -> the migration would miss it.
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
