"""
scripts/normalize_stored_sources.py — One-off backfill for OLD research
=======================================================================
The citation numbers [n] in the report have always been assigned by the
order "most recent sighting first, cap 60" — only the PERSISTED source
list had (until the fix) a different order. This script retroactively
normalizes the stored lists to the same logic (tools.normalize_sources),
so that old reports also get clickable, MATCHING citation links.

Invocation:  cd backend && POSTGRES_HOST=localhost uv run --env-file ../.env \
             python scripts/normalize_stored_sources.py
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlmodel import select

from app.agent.tools import normalize_sources  # noqa: E402
from app.core.db import AsyncSessionLocal  # noqa: E402
from app.models.research_project import ResearchProject  # noqa: E402


async def main() -> None:
    async with AsyncSessionLocal() as session:
        result = await session.exec(
            select(ResearchProject).where(ResearchProject.sources.is_not(None))  # type: ignore[union-attr]
        )
        projects = list(result.all())
        changed = 0
        for p in projects:
            normalized = normalize_sources(list(p.sources or []))
            if normalized != list(p.sources or []):
                p.sources = normalized
                session.add(p)
                changed += 1
        await session.commit()
        print(f"{len(projects)} Projekte geprüft, {changed} Quellenlisten normiert.")


if __name__ == "__main__":
    asyncio.run(main())
