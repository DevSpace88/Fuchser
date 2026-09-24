"""
scripts/recover_old_sources.py — Best-Effort-Wiederherstellung alter Zitat-Links
================================================================================
PROBLEM: Reports aus der Buggy-Ära (Dreifach-Synthese + 60er-Cap) zitieren
Nummern bis [169], aber in research_projects.sources liegen nur 60 Einträge
(falsch sortiert) -> die meisten Zitate sind nicht verlinkbar.

IDEE: Die LangGraph-Checkpoints enthalten den VOLLSTÄNDIGEN sources-Kanal des
Threads (unkappt, mit Duplikaten, in Ankunfts-Reihenfolge). Daraus lassen
sich die historischen Nummerierungsschemata rekonstruieren:
    * Era A ("first-seen", kein Cap): Duplikate entfernen, ERSTE Sichtung
      behält — präfix-stabil über Synthese-Runden hinweg.
    * Era B ("last-occurrence", Cap 60): die aktuelle normalize_sources.
Wir probieren beide (+ die gespeicherte Liste), nehmen die mit den MEISTEN
auflösbaren Zitaten und speichern sie.

Unauflösbare Nummern (halluziniert oder Liste weg) bleiben ehrlich unlinked.
"""

import asyncio
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlmodel import select

from app.agent.graph import build_research_graph
from app.agent.persistence import init_checkpointer
from app.agent.tools import normalize_sources
from app.core.db import AsyncSessionLocal
from app.models.research_project import ResearchProject


def dedupe_first_seen(sources: list) -> list:
    """Era-A-Nummerierung: erste Sichtung gewinnt, Reihenfolge = Ankunft."""
    seen: dict[str, dict] = {}
    for s in sources:
        url = s.get("url", "")
        if url and url not in seen:
            seen[url] = s
    return list(seen.values())


def resolvable(report: str, sources: list) -> int:
    """Wie viele [n]-Zitate zeigen auf eine existierende Quelle?"""
    nums = [int(n) for n in re.findall(r"\[(\d{1,3})\]", report or "")]
    return sum(1 for n in nums if 1 <= n <= len(sources))


async def main(apply: bool) -> None:
    saver = await init_checkpointer()
    graph = build_research_graph(checkpointer=saver)

    async with AsyncSessionLocal() as session:
        result = await session.exec(select(ResearchProject))
        projects = list(result.all())

        for p in projects:
            if not p.report or not p.sources:
                continue
            nums = re.findall(r"\[(\d{1,3})\]", p.report)
            if not nums:
                continue

            # Vollständige Thread-Quellen aus dem Checkpoint holen.
            try:
                snapshot = await graph.aget_state(
                    {"configurable": {"thread_id": str(p.thread_id)}}
                )
                thread_sources = list(snapshot.values.get("sources", [])) if snapshot else []
            except Exception:  # noqa: BLE001
                thread_sources = []

            candidates = {
                "gespeichert": list(p.sources),
                "era-A (first-seen)": dedupe_first_seen(thread_sources),
                "era-B (normalize)": normalize_sources(thread_sources),
            }
            best_name, best_list = max(
                candidates.items(), key=lambda kv: resolvable(p.report, kv[1])
            )
            total = len(nums)
            now = resolvable(p.report, list(p.sources))
            best = resolvable(p.report, best_list)

            print(
                f"{p.question[:45]:<47} Zitate={total:<3} "
                f"jetzt={now:<3} -> best ({best_name})={best:<3} "
                f"[Quellen: {len(list(p.sources))} -> {len(best_list)}]"
            )
            if apply and best_list != list(p.sources) and best >= now:
                p.sources = best_list
                session.add(p)

        if apply:
            await session.commit()
            print("\nAngewendet.")


if __name__ == "__main__":
    asyncio.run(main(apply="--apply" in sys.argv))
