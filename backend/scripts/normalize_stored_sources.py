"""
scripts/normalize_stored_sources.py — Einmaliges Backfill für ALTE Recherchen
===============================================================================
Die Zitat-Nummern [n] im Report wurden schon immer nach der Reihenfolge
"neueste Sichtung zuerst, Cap 60" vergeben — nur die PERSISTIERTE
Quellenliste hatte (bis zum Fix) eine andere Reihenfolge. Dieses Skript
normiert die gespeicherten Listen nachträglich auf dieselbe Logik
(tools.normalize_sources), sodass auch alte Reports klickbare,
STIMMENDE Zitat-Links bekommen.

Ausführung:  cd backend && POSTGRES_HOST=localhost uv run --env-file ../.env \
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
