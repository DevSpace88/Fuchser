"""
core/seed.py — Bequemlichkeits-Funktion: legt den ersten Admin an.
==================================================================

Damit man nach einem frischen Setup sofort testen kann, legen wir beim Start
einen Admin-User aus den SEED_ADMIN_*-Variablen an (sofern gesetzt und sofern
noch nicht vorhanden).

In Produktion kann man das abschalten, indem man die .env-Variablen leer lässt.
"""

from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.core.config import settings
from app.core.security import hash_password
from app.models.user import User, UserRole


async def seed_admin(session: AsyncSession) -> None:
    """
    Legt — falls konfiguriert — den ersten Admin-User an.

    Idempotent: existiert die E-Mail schon, passiert nichts (kein Fehler).
    Das ist wichtig, weil die Funktion bei JEDEM Start läuft.
    """
    if not settings.seed_admin_email or not settings.seed_admin_password:
        # Keine Seed-Werte gesetzt -> nichts tun.
        return

    existing = (
        await session.exec(select(User).where(User.email == settings.seed_admin_email))
    ).first()
    if existing is not None:
        # Gibt es schon -> nichts tun.
        return

    admin = User(
        email=settings.seed_admin_email,
        hashed_password=hash_password(settings.seed_admin_password),
        full_name="Seed-Admin",
        role=UserRole.ADMIN,
        is_active=True,
    )
    session.add(admin)
    await session.commit()
