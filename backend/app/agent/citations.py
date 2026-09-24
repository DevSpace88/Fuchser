"""
agent/citations.py — Wissenschaftliche Zitierformen (Phase 2)
==============================================================

Deterministische Formatierung IN CODE (nicht per LLM — Zitierstile sind
Regelwerke, keine Kreativität): APA 7 und IEEE. Das LLM zitiert im Text
als [n] (Registry-Nummer); hier entsteht daraus das Literaturverzeichnis.

Quellen sind Web-Quellen (URL, Titel, Snippet) — für Web-Dokumente im
APA-/IEEE-Stil nutzen wir Titel + Jahr (aus URL geschätzt, wenn unbekannt)
+ URL. Für echte akademische Arbeiten wären DOI/Autor-Metadaten der
nächste Ausbauschritt (z. B. via Semantic-Scholar-Anbindung).
"""

import re


# Hostnamen als "Autor"-Ersatz (z. B. "docs.langchain.com") — Web-Quellen
# haben selten Autor-Metadaten; APA erlaubt institutional authors.
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
    # Jahreszahl aus URL raten (viele Artikel/Changelogs tragen sie im Pfad)
    match = re.search(r"/(20[0-2]\d)/", source.get("url", ""))
    if match:
        return match.group(1)
    return "o. D." if _style_hint(source) == "apa" else "n.d."


def _style_hint(source: dict) -> str:
    return source.get("_style_hint", "ieee")


def format_bibliography_entry(source: dict, number: int, style: str) -> str:
    """Formatiert EINE Quelle im gewählten Stil."""
    title = (source.get("title") or "Ohne Titel").strip().rstrip(".")
    url = (source.get("url") or "").strip()
    site = _site_from_url(url)
    year = _year_from_source(source)

    if style == "apa":
        # APA 7 für Webseiten: Autor (institutionell). (Jahr). Titel. URL
        return f"{site}. ({year}). *{title}.* {url}".strip()

    if style == "ieee":
        # IEEE für Online-Quellen: [n] Autor/Titel [Online]. Verfügbar: URL
        return f"[{number}] {site}: “{title}” [Online]. Verfügbar: {url}"

    # Fallback: nummeriert, neutral
    return f"[{number}] {title} — {url}"


def format_bibliography(sources: list[dict], style: str) -> str:
    """Das komplette Literaturverzeichnis als Markdown."""
    heading = {
        "apa": "## Literaturverzeichnis (APA 7)",
        "ieee": "## Literaturverzeichnis (IEEE)",
    }.get(style, "## Quellen")
    if not sources:
        return heading + "\n\n*(keine Quellen gesammelt)*\n"
    lines = [format_bibliography_entry(s, i + 1, style) for i, s in enumerate(sources)]
    return heading + "\n\n" + "\n".join(lines) + "\n"


def inline_citation_marker(number: int, style: str) -> str:
    """Wie eine In-Text-Zitation [n] im Zielstil dargestellt wird."""
    if style == "apa":
        return f"({number})"  # vereinfachte Zähl-Form; Autoren-Jahr folgt mit Metadaten
    return f"[{number}]"
