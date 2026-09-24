"""
agent/citations.py — academic citation styles (Phase 2)
==============================================================

Deterministic formatting IN CODE (not via LLM — citation styles are rule
systems, not creativity): APA 7 and IEEE. The LLM cites in text as [n]
(registry number); the bibliography is built from those here.

Sources are web sources (URL, title, snippet) — for web documents in
APA/IEEE style we use title + year (estimated from the URL if unknown)
+ URL. For real academic papers, DOI/author metadata would be the next
extension step (e.g. via a Semantic Scholar integration).
"""

import re


# Hostname as an "author" substitute (e.g. "docs.langchain.com") — web
# sources rarely have author metadata; APA allows institutional authors.
def _site_from_url(url: str) -> str:
    try:
        host = url.split("//", 1)[1].split("/", 1)[0]
        return host.removeprefix("www.")
    except (IndexError, AttributeError):
        return url or "Unbekannte Quelle"


def _year_from_source(source: dict) -> str:
    year = source.get("year")
    if year:
        return str(year)
    # Guess the year from the URL (many articles/changelogs carry it in the path)
    match = re.search(r"/(20[0-2]\d)/", source.get("url", ""))
    if match:
        return match.group(1)
    return "o. D." if _style_hint(source) == "apa" else "n.d."


def _style_hint(source: dict) -> str:
    return source.get("_style_hint", "ieee")


def format_bibliography_entry(source: dict, number: int, style: str) -> str:
    """Formats ONE source in the chosen style."""
    title = (source.get("title") or "Ohne Titel").strip().rstrip(".")
    url = (source.get("url") or "").strip()
    site = _site_from_url(url)
    year = _year_from_source(source)

    if style == "apa":
        # APA 7 for web pages: (institutional) author. (Year). Title. URL
        return f"{site}. ({year}). *{title}.* {url}".strip()

    if style == "ieee":
        # IEEE for online sources: [n] author/title [Online]. Verfügbar: URL
        return f"[{number}] {site}: “{title}” [Online]. Verfügbar: {url}"

    # Fallback: numbered, neutral
    return f"[{number}] {title} — {url}"


def format_bibliography(sources: list[dict], style: str) -> str:
    """The complete bibliography as Markdown."""
    heading = {
        "apa": "## Literaturverzeichnis (APA 7)",
        "ieee": "## Literaturverzeichnis (IEEE)",
    }.get(style, "## Quellen")
    if not sources:
        return heading + "\n\n*(keine Quellen gesammelt)*\n"
    lines = [format_bibliography_entry(s, i + 1, style) for i, s in enumerate(sources)]
    return heading + "\n\n" + "\n".join(lines) + "\n"


def inline_citation_marker(number: int, style: str) -> str:
    """How an in-text citation [n] is rendered in the target style."""
    if style == "apa":
        return f"({number})"  # simplified counting form; author-year once metadata exists
    return f"[{number}]"
