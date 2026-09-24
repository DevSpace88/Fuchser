"""
core/seed.py — Convenience function: creates the first admin.
=============================================================

So you can test right away after a fresh setup, we create an admin user at
startup from the SEED_ADMIN_* variables (if set, and if it does not exist
yet).

In production you can turn this off by leaving the .env variables empty.
"""

from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.core.config import settings
from app.core.security import hash_password
from app.models.user import User, UserRole


async def seed_admin(session: AsyncSession) -> None:
    """
    Creates — if configured — the first admin user.

    Idempotent: if the email already exists, nothing happens (no error).
    That matters because this function runs on EVERY startup.
    """
    if not settings.seed_admin_email or not settings.seed_admin_password:
        # No seed values set -> do nothing.
        return

    existing = (
        await session.exec(select(User).where(User.email == settings.seed_admin_email))
    ).first()
    if existing is not None:
        # Already exists -> do nothing.
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
