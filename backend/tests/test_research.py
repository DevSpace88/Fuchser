"""
tests/test_research.py — Recherche-API (Stufe 1, PLAN.md)
=========================================================

Getestete Flows:
  1) Anlegen + Listen + Detail (Happy Path, mit Auth).
  2) Ownership: User B sieht/löscht User A's Projekt nicht (404).
  3) Validierung: leere Frage -> 422; ohne Token -> 401.
  4) SSE-Run im Echo-Modus (kein API-Key nötig): token-Events + done,
     danach steht der Report persistent im Projekt.

Der Echo-Modus (agent/llm.py liefert None ohne Key) macht diese Tests
deterministisch und offline — genau der Grund, warum das LLM injiziert wird.
"""

import json

import pytest


def auth_header(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# ============================================================================
# 0) RECONNECT-ENTSHEIDUNG (Redis-Pfad): niemals doppelt einreihen
# ============================================================================
def test_redis_run_action_reconnect_vs_push():
    """Seiten-Reload darf einen laufenden Task NICHT ein zweites Mal
    einreihen (sonst läuft der komplette Report doppelt = doppelte Kosten).
    Ein verwaister Lauf (Status running, Worker tot) MUSS neu eingereiht
    werden — mit Resume ab dem letzten persistierten Kapitel."""
    from app.services.research_service import _redis_run_action

    # Task aktiv (in Queue oder Worker am Leben) -> nur abonnieren
    assert _redis_run_action("running", True) == "subscribe"
    assert _redis_run_action("queued", True) == "subscribe"
    # Kein aktiver Task -> einreihen (Start, Resume nach Fehler, Re-Run)
    assert _redis_run_action("queued", False) == "push"
    assert _redis_run_action("error", False) == "push"
    assert _redis_run_action("done", False) == "push"
    # Verwaister Lauf: Status "running", aber Worker gestorben -> neu einreihen
    assert _redis_run_action("running", False) == "push"


# ============================================================================
# 0b) PDF-EXPORT: Gliederung/Abstract/TOC — auch als TEILBERICHT
# ============================================================================
def _pdf_project(**overrides):
    """Minimal-Projekt für die PDF-Unit-Tests (kein HTTP nötig)."""
    from datetime import UTC, datetime
    from types import SimpleNamespace

    base = dict(
        report=None,
        title=None,
        question="Wie ist der Brandenburger Wohnungsmarkt?",
        status="error",
        created_at=datetime.now(UTC),
        sources=[{"title": "Quelle 1", "url": "https://example.com", "snippet": ""}],
        outline={
            "title": "Brandenburger Wohnungsmarkt",
            "abstract": "Eine Analyse.",
            "chapters": [{"title": "K1"}, {"title": "K2"}, {"title": "K3"}],
        },
        chapters=[{"title": "K1", "content": "Inhalt von K1 mit Zitat [1]."}],
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def test_pdf_markdown_partial_includes_outline():
    """Teilbericht (abgebrochener Lauf): Gliederung am Anfang, geschriebene
    Kapitel enthalten, fehlende Kapitel im TOC markiert."""
    from app.services.research_service import _report_markdown_for_pdf

    md = _report_markdown_for_pdf(_pdf_project())
    assert "# Brandenburger Wohnungsmarkt" in md
    assert "**Abstract:** Eine Analyse." in md
    assert "## Inhaltsverzeichnis" in md
    assert "1. K1" in md
    assert "2. K2 *(geplant — noch nicht geschrieben)*" in md
    assert "## 1. K1" in md and "Inhalt von K1" in md
    assert "Teilbericht" in md


def test_pdf_markdown_prefers_final_report():
    from app.services.research_service import _report_markdown_for_pdf

    p = _pdf_project(report="# Finaler Bericht\n\n## Inhaltsverzeichnis\n1. K1")
    assert _report_markdown_for_pdf(p).startswith("# Finaler Bericht")


def test_pdf_markdown_nothing_available_raises():
    import pytest as _pytest

    from app.services.research_service import _report_markdown_for_pdf

    with _pytest.raises(ValueError):
        _report_markdown_for_pdf(_pdf_project(chapters=[], outline=None))


def test_report_to_pdf_partial_project_renders():
    """Smoke: Aus einem Teilergebnis entsteht ein echtes PDF."""
    from app.services.research_service import report_to_pdf

    pdf = report_to_pdf(_pdf_project())
    assert pdf.startswith(b"%PDF")


def test_clean_report_text_removes_duplicate_headings_and_denglisch():
    from app.services.research_service import _clean_report_text

    raw = (
        "## 3. US-Konzerne und Scale-ups mit deutschen Entwicklungsstandorten\n\n"
        "## US-Konzerne und Scale-ups mit deutschen Entwicklungsstandorten\n\n"
        "Für Entwickler mit Fokus auf Compensation-Modellen und Compensation-Benchmarks."
    )
    cleaned = _clean_report_text(raw)
    # Doppelte Überschrift entfernt
    assert cleaned.count("US-Konzerne und Scale-ups") == 1
    assert "## 3. US-Konzerne und Scale-ups mit deutschen Entwicklungsstandorten" in cleaned
    # Denglisch ersetzt
    assert "Compensation-Modellen" not in cleaned
    assert "Vergütungsmodellen" in cleaned
    assert "Compensation-Benchmarks" not in cleaned
    assert "Gehaltsbenchmarks" in cleaned


async def _register_and_login(client, email: str) -> str:
    resp = await client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": "supersecret"},
    )
    assert resp.status_code == 201, resp.text
    resp = await client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": "supersecret"},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["access_token"]


# ============================================================================
# 1) HAPPY PATH: anlegen → listen → detail
# ============================================================================
@pytest.mark.asyncio
async def test_create_list_get_research(client):
    token = await _register_and_login(client, "alice@example.com")

    # --- Anlegen ---
    resp = await client.post(
        "/api/v1/research",
        json={"question": "Wie beeinflusst EU-Regulierung KI-Startups?"},
        headers=auth_header(token),
    )
    assert resp.status_code == 201, resp.text
    project = resp.json()
    assert project["question"].startswith("Wie beeinflusst")
    assert project["status"] == "queued"
    assert project["report"] is None
    assert project["thread_id"]  # LangGraph-Thread existiert

    # --- Listen ---
    resp = await client.get("/api/v1/research", headers=auth_header(token))
    assert resp.status_code == 200
    assert len(resp.json()) == 1

    # --- Detail ---
    resp = await client.get(f"/api/v1/research/{project['id']}", headers=auth_header(token))
    assert resp.status_code == 200
    assert resp.json()["id"] == project["id"]


# ============================================================================
# 2) OWNERSHIP: fremde Projekte sind unsichtbar (404, nicht 403)
# ============================================================================
@pytest.mark.asyncio
async def test_ownership_isolation(client):
    token_a = await _register_and_login(client, "alice@example.com")
    token_b = await _register_and_login(client, "bob@example.com")

    resp = await client.post(
        "/api/v1/research",
        json={"question": "Alles über LangGraph"},
        headers=auth_header(token_a),
    )
    project_id = resp.json()["id"]

    # B sieht es nicht ...
    resp = await client.get(f"/api/v1/research/{project_id}", headers=auth_header(token_b))
    assert resp.status_code == 404

    # ... und B's Liste ist leer.
    resp = await client.get("/api/v1/research", headers=auth_header(token_b))
    assert resp.json() == []

    # Löschen geht auch nicht.
    resp = await client.delete(f"/api/v1/research/{project_id}", headers=auth_header(token_b))
    assert resp.status_code == 404

    # A kann löschen -> danach 404 auch für A.
    resp = await client.delete(f"/api/v1/research/{project_id}", headers=auth_header(token_a))
    assert resp.status_code == 204
    resp = await client.get(f"/api/v1/research/{project_id}", headers=auth_header(token_a))
    assert resp.status_code == 404


# ============================================================================
# 3) VALIDIERUNG & AUTH
# ============================================================================
@pytest.mark.asyncio
async def test_validation_and_auth(client):
    # Ohne Token: 401 (OAuth2-Schema wirft sofort).
    resp = await client.get("/api/v1/research")
    assert resp.status_code == 401

    token = await _register_and_login(client, "alice@example.com")

    # Zu kurze Frage: 422 (Pydantic min_length=3).
    resp = await client.post(
        "/api/v1/research",
        json={"question": "x"},
        headers=auth_header(token),
    )
    assert resp.status_code == 422


# ============================================================================
# 4) SSE-RUN im Echo-Modus: token-Events + done + persistenter Report
# ============================================================================
@pytest.mark.asyncio
async def test_run_sse_echo_mode(client):
    token = await _register_and_login(client, "alice@example.com")

    resp = await client.post(
        "/api/v1/research",
        json={"question": "Was ist LangGraph?"},
        headers=auth_header(token),
    )
    project_id = resp.json()["id"]

    # httpx streamt die SSE-Antwort Chunk für Chunk.
    events: dict[str, list] = {}
    async with client.stream(
        "POST",
        f"/api/v1/research/{project_id}/run",
        headers=auth_header(token),
    ) as resp:
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/event-stream")

        event_name = None
        async for line in resp.aiter_lines():
            if line.startswith("event: "):
                event_name = line[len("event: ") :]
                events.setdefault(event_name, [])
            elif line.startswith("data: ") and event_name:
                events[event_name].append(json.loads(line[len("data: ") :]))

    # Protokoll: mindestens ein status(running)-, token- und done-Event.
    assert any(e["status"] == "running" for e in events["status"])
    assert events["token"], "Echo-Modus muss token-Events liefern"
    assert events["done"][0]["status"] == "done"

    # Nach dem Lauf: Report persistent, Status done.
    resp = await client.get(f"/api/v1/research/{project_id}", headers=auth_header(token))
    project = resp.json()
    assert project["status"] == "done"
    assert "LangGraph" in project["report"]


# ============================================================================
# 5) PDF-DOWNLOAD (echter Datei-Download, kein Druck-Dialog)
# ============================================================================
@pytest.mark.asyncio
async def test_pdf_download_requires_auth(client):
    resp = await client.get("/api/v1/research/00000000-0000-0000-0000-000000000000/pdf")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_pdf_download_409_without_report(client):
    token = await _register_and_login(client, "alice@example.com")
    resp = await client.post(
        "/api/v1/research",
        json={"question": "Ohne Report kein PDF"},
        headers=auth_header(token),
    )
    pid = resp.json()["id"]

    resp = await client.get(f"/api/v1/research/{pid}/pdf", headers=auth_header(token))
    assert resp.status_code == 409  # Projekt ja, Report nein


@pytest.mark.asyncio
async def test_pdf_download_happy_path(client):
    token = await _register_and_login(client, "alice@example.com")
    resp = await client.post(
        "/api/v1/research",
        json={"question": "PDF-Test?"},
        headers=auth_header(token),
    )
    pid = resp.json()["id"]

    # Lauf im Echo-Modus (erzeugt Report ohne API-Key).
    async with client.stream(
        "POST", f"/api/v1/research/{pid}/run", headers=auth_header(token)
    ) as r:
        async for _ in r.aiter_lines():
            pass

    resp = await client.get(f"/api/v1/research/{pid}/pdf", headers=auth_header(token))
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/pdf"
    assert "attachment" in resp.headers["content-disposition"]
    assert resp.content.startswith(b"%PDF")


def test_linkify_citations_unit():
    from app.services.research_service import linkify_citations

    sources = [
        {"title": "BNetzA Regeln", "url": "https://example.com/bnetza"},
        {"title": "VDE Norm", "url": "https://example.com/vde"},
        {"title": "Solar Guide", "url": "https://example.com/solar"},
    ]

    # Single citation
    assert linkify_citations("Balkonkraftwerk erlaubt [1].", sources) == (
        'Balkonkraftwerk erlaubt [1](https://example.com/bnetza "BNetzA Regeln").'
    )

    # Multi citations in brackets
    assert linkify_citations("Regeln [1, 2] und Details [1-3].", sources) == (
        'Regeln [1](https://example.com/bnetza "BNetzA Regeln") '
        '[2](https://example.com/vde "VDE Norm") und Details '
        '[1](https://example.com/bnetza "BNetzA Regeln") '
        '[2](https://example.com/vde "VDE Norm") '
        '[3](https://example.com/solar "Solar Guide").'
    )

    # Code block & inline code should not be changed
    code_text = "Here is `arr[1]` and ```\nval = items[2]\n```."
    assert linkify_citations(code_text, sources) == code_text

    # Already formatted markdown link
    md_link = "Look at [1](https://other.com)."
    assert linkify_citations(md_link, sources) == md_link


# ============================================================================
# 6) STUFE 5: FOLLOW-UPS (Thread-Memory) + TOKEN-USAGE
# ============================================================================
@pytest.mark.asyncio
async def test_followup_reuses_parent_thread(client):
    token = await _register_and_login(client, "alice@example.com")

    resp = await client.post(
        "/api/v1/research",
        json={"question": "Was ist LangGraph?"},
        headers=auth_header(token),
    )
    parent = resp.json()
    assert parent["parent_id"] is None

    # Follow-up-Frage mit Verweis aufs Original …
    resp = await client.post(
        "/api/v1/research",
        json={"question": "Und wie genau funktionieren Checkpointer?", "followup_of": parent["id"]},
        headers=auth_header(token),
    )
    followup = resp.json()
    # … läuft im SELBEN Thread (=> Graph sieht die alten findings).
    assert followup["parent_id"] == parent["id"]
    assert followup["thread_id"] == parent["thread_id"]


@pytest.mark.asyncio
async def test_followup_to_foreign_project_404(client):
    token_a = await _register_and_login(client, "alice@example.com")
    token_b = await _register_and_login(client, "bob@example.com")

    resp = await client.post(
        "/api/v1/research",
        json={"question": "Original von A"},
        headers=auth_header(token_a),
    )
    parent_id = resp.json()["id"]

    # B darf sich nicht an A's Recherche "anhängen".
    resp = await client.post(
        "/api/v1/research",
        json={"question": "Nachfrage von B?", "followup_of": parent_id},
        headers=auth_header(token_b),
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_usage_persisted_after_run(client):
    """Echo-Modus hat kein echtes LLM -> usage bleibt null/leer, aber der
    Lauf darf nicht crashen. (Echte Token testet der Fake-LLM-Graph-Test.)"""
    token = await _register_and_login(client, "alice@example.com")
    resp = await client.post(
        "/api/v1/research",
        json={"question": "Usage-Test?"},
        headers=auth_header(token),
    )
    pid = resp.json()["id"]
    async with client.stream(
        "POST", f"/api/v1/research/{pid}/run", headers=auth_header(token)
    ) as r:
        async for _ in r.aiter_lines():
            pass
    resp = await client.get(f"/api/v1/research/{pid}", headers=auth_header(token))
    assert resp.json()["status"] == "done"


# ============================================================================
# 7) STUFE 5: ADMIN-ENDPUNKT
# ============================================================================
@pytest.mark.asyncio
async def test_admin_list_all_research(client, test_session):
    # Normaler User mit einer Recherche …
    token_user = await _register_and_login(client, "user@example.com")
    await client.post(
        "/api/v1/research",
        json={"question": "Meine geheime Recherche"},
        headers=auth_header(token_user),
    )

    # … sieht die Admin-Liste NICHT (403) …
    resp = await client.get("/api/v1/research/admin/all", headers=auth_header(token_user))
    assert resp.status_code == 403

    # … der Admin sieht sie: admin2 anlegen, einloggen …
    resp = await client.post(
        "/api/v1/auth/register",
        json={"email": "admin2@example.com", "password": "supersecret"},
    )
    assert resp.status_code == 201
    token_admin = (
        await client.post(
            "/api/v1/auth/login",
            json={"email": "admin2@example.com", "password": "supersecret"},
        )
    ).json()["access_token"]

    # … und per Test-Session (dieselbe, die auch der Client-Override nutzt)
    # zum Admin befördern.
    from sqlmodel import select as _select

    from app.models.user import User, UserRole

    user_row = (
        await test_session.exec(_select(User).where(User.email == "admin2@example.com"))
    ).first()
    assert user_row is not None
    user_row.role = UserRole.ADMIN
    test_session.add(user_row)
    await test_session.commit()

    resp = await client.get("/api/v1/research/admin/all", headers=auth_header(token_admin))
    assert resp.status_code == 200
    rows = resp.json()
    assert any(r["question"] == "Meine geheime Recherche" for r in rows)
    assert all("user_email" in r for r in rows)


@pytest.mark.asyncio
async def test_trace_persisted_after_run(client):
    """Stufe 5+: Die Ausführungs-Historie (Timeline fürs Sidepanel) wird
    mitgeschnitten und landet in der DB."""
    token = await _register_and_login(client, "alice@example.com")
    resp = await client.post(
        "/api/v1/research",
        json={"question": "Trace-Test?"},
        headers=auth_header(token),
    )
    pid = resp.json()["id"]
    async with client.stream(
        "POST", f"/api/v1/research/{pid}/run", headers=auth_header(token)
    ) as r:
        async for _ in r.aiter_lines():
            pass

    resp = await client.get(f"/api/v1/research/{pid}", headers=auth_header(token))
    trace = resp.json()["trace"] or []
    kinds = {e["event"] for e in trace}
    # Echo-Lauf: Supervisor/Researcher/Synthesizer/Critic-Node + Phasen.
    assert "node" in kinds
    assert "status" in kinds
    assert any(e.get("node") == "supervisor" for e in trace)
    assert all("t" in e for e in trace)  # Zeitstempel überall


# ============================================================================
# 8) UMBENENNEN (title steuert Anzeige + PDF-Titel)
# ============================================================================
@pytest.mark.asyncio
async def test_rename_research(client):
    token = await _register_and_login(client, "alice@example.com")
    resp = await client.post(
        "/api/v1/research",
        json={"question": "Super lange umständliche Ursprungsfrage zum Testen?"},
        headers=auth_header(token),
    )
    pid = resp.json()["id"]

    # Umbenennen …
    resp = await client.patch(
        f"/api/v1/research/{pid}",
        json={"title": "Kurzer Name"},
        headers=auth_header(token),
    )
    assert resp.status_code == 200
    assert resp.json()["title"] == "Kurzer Name"
    assert resp.json()["question"].startswith("Super lange")  # Frage bleibt!

    # … und zurückgeben an die Liste.
    resp = await client.get("/api/v1/research", headers=auth_header(token))
    assert resp.json()[0]["title"] == "Kurzer Name"


@pytest.mark.asyncio
async def test_rename_foreign_project_404(client):
    token_a = await _register_and_login(client, "alice@example.com")
    token_b = await _register_and_login(client, "bob@example.com")
    pid = (
        await client.post(
            "/api/v1/research",
            json={"question": "A's Recherche"},
            headers=auth_header(token_a),
        )
    ).json()["id"]

    resp = await client.patch(
        f"/api/v1/research/{pid}",
        json={"title": "B's Hackversuch"},
        headers=auth_header(token_b),
    )
    assert resp.status_code == 404


# ============================================================================
# 9) UNTERHALTUNGEN (Chat-Modell wie Google AI Studio)
# ============================================================================
@pytest.mark.asyncio
async def test_new_question_creates_conversation(client):
    token = await _register_and_login(client, "alice@example.com")
    resp = await client.post(
        "/api/v1/research",
        json={"question": "Erste Frage im neuen Chat-Modell?"},
        headers=auth_header(token),
    )
    assert resp.status_code == 201
    conv_id = resp.json()["conversation_id"]
    assert conv_id  # automatisch angelegt

    convs = await client.get("/api/v1/conversations", headers=auth_header(token))
    assert convs.status_code == 200
    assert convs.json()[0]["message_count"] == 1
    assert convs.json()[0]["title"].startswith("Erste Frage")


@pytest.mark.asyncio
async def test_followup_lands_in_same_conversation(client):
    token = await _register_and_login(client, "alice@example.com")
    first = (
        await client.post(
            "/api/v1/research", json={"question": "Chat-Start?"}, headers=auth_header(token)
        )
    ).json()

    # Nachfrage (expliziter Follow-up) -> SELBE Unterhaltung
    second = (
        await client.post(
            "/api/v1/research",
            json={"question": "Und noch eine Nachfrage?", "followup_of": first["id"]},
            headers=auth_header(token),
        )
    ).json()
    assert second["conversation_id"] == first["conversation_id"]

    # Frage mit conversation_id -> auch dieselbe
    third = (
        await client.post(
            "/api/v1/research",
            json={
                "question": "Dritte Nachricht im Chat?",
                "conversation_id": first["conversation_id"],
            },
            headers=auth_header(token),
        )
    ).json()
    assert third["conversation_id"] == first["conversation_id"]

    # Historie: EINE Unterhaltung mit DREI Nachrichten
    convs = (await client.get("/api/v1/conversations", headers=auth_header(token))).json()
    assert len(convs) == 1
    assert convs[0]["message_count"] == 3

    detail = await client.get(
        f"/api/v1/conversations/{first['conversation_id']}", headers=auth_header(token)
    )
    assert [m["question"] for m in detail.json()["messages"]] == [
        "Chat-Start?",
        "Und noch eine Nachfrage?",
        "Dritte Nachricht im Chat?",
    ]


@pytest.mark.asyncio
async def test_rename_conversation(client):
    token = await _register_and_login(client, "alice@example.com")
    conv_id = (
        await client.post(
            "/api/v1/research", json={"question": "Langweiliger Titel?"}, headers=auth_header(token)
        )
    ).json()["conversation_id"]

    resp = await client.patch(
        f"/api/v1/conversations/{conv_id}",
        json={"title": "Spannender Name"},
        headers=auth_header(token),
    )
    assert resp.status_code == 200
    assert resp.json()["title"] == "Spannender Name"


@pytest.mark.asyncio
async def test_conversation_isolation(client):
    token_a = await _register_and_login(client, "alice@example.com")
    token_b = await _register_and_login(client, "bob@example.com")
    conv_id = (
        await client.post(
            "/api/v1/research", json={"question": "A's Chat"}, headers=auth_header(token_a)
        )
    ).json()["conversation_id"]

    resp = await client.get(f"/api/v1/conversations/{conv_id}", headers=auth_header(token_b))
    assert resp.status_code == 404
    resp = await client.post(
        "/api/v1/research",
        json={"question": "B schleicht sich ein?", "conversation_id": conv_id},
        headers=auth_header(token_b),
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_delete_conversation_cascades(client):
    """Löschen einer Unterhaltung entfernt alle Nachrichten + Dokumente."""
    token = await _register_and_login(client, "alice@example.com")
    conv_id = (
        await client.post(
            "/api/v1/research", json={"question": "Zu löschender Chat?"}, headers=auth_header(token)
        )
    ).json()["conversation_id"]
    second = (
        await client.post(
            "/api/v1/research",
            json={"question": "Zweite Nachricht?", "conversation_id": conv_id},
            headers=auth_header(token),
        )
    ).json()
    await client.post(
        f"/api/v1/research/{second['id']}/documents",
        headers=auth_header(token),
        files={"files": ("doc.txt", b"inhalt", "text/plain")},
    )

    resp = await client.delete(f"/api/v1/conversations/{conv_id}", headers=auth_header(token))
    assert resp.status_code == 204

    # Unterhaltung weg …
    resp = await client.get(f"/api/v1/conversations/{conv_id}", headers=auth_header(token))
    assert resp.status_code == 404
    # … und die Nachrichten mit ihr (Liste leer).
    resp = await client.get("/api/v1/research", headers=auth_header(token))
    assert resp.json() == []

    # Fremde können nicht löschen (404 statt 403 — nichts verraten).
    token_b = await _register_and_login(client, "bob@example.com")
    conv_b = (
        await client.post(
            "/api/v1/research", json={"question": "B's Chat"}, headers=auth_header(token_b)
        )
    ).json()["conversation_id"]
    resp = await client.delete(f"/api/v1/conversations/{conv_b}", headers=auth_header(token))
    assert resp.status_code == 404
