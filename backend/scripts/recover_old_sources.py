"""
scripts/recover_old_sources.py — Best-effort recovery of old citation links
===========================================================================
PROBLEM: reports from the buggy era (triple synthesis + cap of 60) cite
numbers up to [169], but research_projects.sources only holds 60 entries
(wrongly sorted) -> most citations cannot be linked.

IDEA: the LangGraph checkpoints contain the COMPLETE sources channel of the
thread (uncapped, with duplicates, in arrival order). The historical
numbering schemes can be reconstructed from it:
    * Era A ("first-seen", no cap): remove duplicates, the FIRST sighting
      wins — prefix-stable across synthesis rounds.
    * Era B ("last-occurrence", cap 60): the current normalize_sources.
We try both (+ the stored list), pick the one resolving the MOST citations
and save it.

Unresolvable numbers (hallucinated or list gone) honestly stay unlinked.
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
    """Era-A numbering: first sighting wins, order = arrival."""
    seen: dict[str, dict] = {}
    for s in sources:
        url = s.get("url", "")
        if url and url not in seen:
            seen[url] = s
    return list(seen.values())


def resolvable(report: str, sources: list) -> int:
    """How many [n] citations point to an existing source?"""
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

            # Fetch the full thread sources from the checkpoint.
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
