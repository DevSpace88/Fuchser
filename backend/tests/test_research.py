"""
tests/test_research.py — Research API (Stage 1, PLAN.md)
========================================================

Flows under test:
  1) Create + list + detail (happy path, with auth).
  2) Ownership: user B cannot see/delete user A's project (404).
  3) Validation: empty question -> 422; without a token -> 401.
  4) SSE run in echo mode (no API key needed): token events + done,
     afterwards the report is stored persistently on the project.

The echo mode (agent/llm.py returns None without a key) makes these tests
deterministic and offline — exactly the reason why the LLM is injected.
"""

import json

import pytest


def auth_header(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# ============================================================================
# 0) RECONNECT DECISION (Redis path): never enqueue twice
# ============================================================================
def test_redis_run_action_reconnect_vs_push():
    """A page reload must NOT enqueue a running task a second time
    (otherwise the whole report runs twice = double the cost).
    An orphaned run (status running, worker dead) MUST be enqueued
    again — with a resume from the last persisted chapter."""
    from app.services.research_service import _redis_run_action

    # Task active (in the queue or the worker alive) -> only subscribe
    assert _redis_run_action("running", True) == "subscribe"
    assert _redis_run_action("queued", True) == "subscribe"
    # No active task -> enqueue (start, resume after an error, re-run)
    assert _redis_run_action("queued", False) == "push"
    assert _redis_run_action("error", False) == "push"
    assert _redis_run_action("done", False) == "push"
    # Orphaned run: status "running" but the worker died -> enqueue again
    assert _redis_run_action("running", False) == "push"


# ============================================================================
# 0b) PDF EXPORT: outline/abstract/TOC — also as a PARTIAL REPORT
# ============================================================================
def _pdf_project(**overrides):
    """Minimal project for the PDF unit tests (no HTTP needed)."""
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
    """Partial report (aborted run): outline at the top, written
    chapters included, missing chapters marked in the TOC."""
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
    """Smoke test: a real PDF is produced from a partial result."""
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
    # Duplicate heading removed
    assert cleaned.count("US-Konzerne und Scale-ups") == 1
    assert "## 3. US-Konzerne und Scale-ups mit deutschen Entwicklungsstandorten" in cleaned
    # Denglish replaced
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
# 1) HAPPY PATH: create → list → detail
# ============================================================================
@pytest.mark.asyncio
async def test_create_list_get_research(client):
    token = await _register_and_login(client, "alice@example.com")

    # --- Create ---
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
    assert project["thread_id"]  # LangGraph thread exists

    # --- List ---
    resp = await client.get("/api/v1/research", headers=auth_header(token))
    assert resp.status_code == 200
    assert len(resp.json()) == 1

    # --- Detail ---
    resp = await client.get(f"/api/v1/research/{project['id']}", headers=auth_header(token))
    assert resp.status_code == 200
    assert resp.json()["id"] == project["id"]


# ============================================================================
# 2) OWNERSHIP: other people's projects are invisible (404, not 403)
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

    # B cannot see it ...
    resp = await client.get(f"/api/v1/research/{project_id}", headers=auth_header(token_b))
    assert resp.status_code == 404

    # ... and B's list is empty.
    resp = await client.get("/api/v1/research", headers=auth_header(token_b))
    assert resp.json() == []

    # Deleting does not work either.
    resp = await client.delete(f"/api/v1/research/{project_id}", headers=auth_header(token_b))
    assert resp.status_code == 404

    # A can delete -> afterwards 404 for A too.
    resp = await client.delete(f"/api/v1/research/{project_id}", headers=auth_header(token_a))
    assert resp.status_code == 204
    resp = await client.get(f"/api/v1/research/{project_id}", headers=auth_header(token_a))
    assert resp.status_code == 404


# ============================================================================
# 3) VALIDATION & AUTH
# ============================================================================
@pytest.mark.asyncio
async def test_validation_and_auth(client):
    # Without a token: 401 (the OAuth2 schema throws immediately).
    resp = await client.get("/api/v1/research")
    assert resp.status_code == 401

    token = await _register_and_login(client, "alice@example.com")

    # Question too short: 422 (Pydantic min_length=3).
    resp = await client.post(
        "/api/v1/research",
        json={"question": "x"},
        headers=auth_header(token),
    )
    assert resp.status_code == 422


# ============================================================================
# 4) SSE RUN in echo mode: token events + done + persistent report
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

    # httpx streams the SSE response chunk by chunk.
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

    # Protocol: at least one status(running), token and done event.
    assert any(e["status"] == "running" for e in events["status"])
    assert events["token"], "Echo-Modus muss token-Events liefern"
    assert events["done"][0]["status"] == "done"

    # After the run: report persisted, status done.
    resp = await client.get(f"/api/v1/research/{project_id}", headers=auth_header(token))
    project = resp.json()
    assert project["status"] == "done"
    assert "LangGraph" in project["report"]


# ============================================================================
# 5) PDF DOWNLOAD (real file download, no print dialog)
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
    assert resp.status_code == 409  # project yes, report no


@pytest.mark.asyncio
async def test_pdf_download_happy_path(client):
    token = await _register_and_login(client, "alice@example.com")
    resp = await client.post(
        "/api/v1/research",
        json={"question": "PDF-Test?"},
        headers=auth_header(token),
    )
    pid = resp.json()["id"]

    # Run in echo mode (produces a report without an API key).
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
# 6) STAGE 5: FOLLOW-UPS (thread memory) + TOKEN USAGE
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

    # Follow-up question referencing the original …
    resp = await client.post(
        "/api/v1/research",
        json={"question": "Und wie genau funktionieren Checkpointer?", "followup_of": parent["id"]},
        headers=auth_header(token),
    )
    followup = resp.json()
    # … runs in the SAME thread (=> the graph sees the old findings).
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

    # B must not "attach" itself to A's research.
    resp = await client.post(
        "/api/v1/research",
        json={"question": "Nachfrage von B?", "followup_of": parent_id},
        headers=auth_header(token_b),
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_usage_persisted_after_run(client):
    """Echo mode has no real LLM -> usage stays null/empty, but the
    run must not crash. (Real tokens are tested by the fake-LLM graph test.)"""
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
# 7) STAGE 5: ADMIN ENDPOINT
# ============================================================================
@pytest.mark.asyncio
async def test_admin_list_all_research(client, test_session):
    # A normal user with one research …
    token_user = await _register_and_login(client, "user@example.com")
    await client.post(
        "/api/v1/research",
        json={"question": "Meine geheime Recherche"},
        headers=auth_header(token_user),
    )

    # … does NOT see the admin list (403) …
    resp = await client.get("/api/v1/research/admin/all", headers=auth_header(token_user))
    assert resp.status_code == 403

    # … the admin sees it: create admin2, log in …
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

    # … and promote them to admin via the test session (the same one
    # the client override uses).
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
    """Stage 5+: the execution history (timeline for the side panel) is
    recorded and lands in the DB."""
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
    # Echo run: supervisor/researcher/synthesizer/critic nodes + phases.
    assert "node" in kinds
    assert "status" in kinds
    assert any(e.get("node") == "supervisor" for e in trace)
    assert all("t" in e for e in trace)  # timestamps everywhere


# ============================================================================
# 8) RENAME (title controls display + PDF title)
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

    # Rename …
    resp = await client.patch(
        f"/api/v1/research/{pid}",
        json={"title": "Kurzer Name"},
        headers=auth_header(token),
    )
    assert resp.status_code == 200
    assert resp.json()["title"] == "Kurzer Name"
    assert resp.json()["question"].startswith("Super lange")  # the question stays!

    # … and check it back via the list.
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
# 9) CONVERSATIONS (chat model like Google AI Studio)
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
    assert conv_id  # created automatically

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

    # Follow-up question (explicit followup) -> SAME conversation
    second = (
        await client.post(
            "/api/v1/research",
            json={"question": "Und noch eine Nachfrage?", "followup_of": first["id"]},
            headers=auth_header(token),
        )
    ).json()
    assert second["conversation_id"] == first["conversation_id"]

    # Question with conversation_id -> the same one too
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

    # History: ONE conversation with THREE messages
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
    """Deleting a conversation removes all messages + documents."""
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

    # Conversation gone …
    resp = await client.get(f"/api/v1/conversations/{conv_id}", headers=auth_header(token))
    assert resp.status_code == 404
    # … and the messages with it (list empty).
    resp = await client.get("/api/v1/research", headers=auth_header(token))
    assert resp.json() == []

    # Strangers cannot delete (404 instead of 403 — reveal nothing).
    token_b = await _register_and_login(client, "bob@example.com")
    conv_b = (
        await client.post(
            "/api/v1/research", json={"question": "B's Chat"}, headers=auth_header(token_b)
        )
    ).json()["conversation_id"]
    resp = await client.delete(f"/api/v1/conversations/{conv_b}", headers=auth_header(token))
    assert resp.status_code == 404
