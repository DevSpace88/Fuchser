"""
tests/test_documents.py — Dokumenten-Upload + document_search-Tool
===================================================================
Tested ohne echte schwere Dateien: TXT direkt, DOCX mit python-docx
generiert, Scan-PDF-Fall simuliert (Extraktion liefert nichts -> 422).
"""

import io

import pytest
from docx import Document as DocxDocument

from app.agent.tools import make_document_search_tool
from tests.test_research import _register_and_login, auth_header


def _make_docx(text: str) -> bytes:
    """Erzeugt eine echte DOCX im Speicher."""
    doc = DocxDocument()
    doc.add_paragraph(text)
    buffer = io.BytesIO()
    doc.save(buffer)
    return buffer.getvalue()


@pytest.mark.asyncio
async def test_upload_txt_and_list(client):
    token = await _register_and_login(client, "alice@example.com")
    pid = (
        await client.post(
            "/api/v1/research",
            json={"question": "Dokumenten-Test?"},
            headers=auth_header(token),
        )
    ).json()["id"]

    resp = await client.post(
        f"/api/v1/research/{pid}/documents",
        headers=auth_header(token),
        files={"files": ("notizen.txt", b"Der Fuchs jagt Beeren im Wald.", "text/plain")},
    )
    assert resp.status_code == 201, resp.text
    doc = resp.json()[0]
    assert doc["filename"] == "notizen.txt"
    assert doc["char_count"] > 0

    resp = await client.get(f"/api/v1/research/{pid}/documents", headers=auth_header(token))
    assert len(resp.json()) == 1


@pytest.mark.asyncio
async def test_upload_docx(client):
    token = await _register_and_login(client, "alice@example.com")
    pid = (
        await client.post(
            "/api/v1/research", json={"question": "Docx?"}, headers=auth_header(token)
        )
    ).json()["id"]

    resp = await client.post(
        f"/api/v1/research/{pid}/documents",
        headers=auth_header(token),
        files={
            "files": (
                "bericht.docx",
                _make_docx("Laravel Gehälter steigen 2026 deutlich."),
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            )
        },
    )
    assert resp.status_code == 201
    assert resp.json()[0]["char_count"] > 20


@pytest.mark.asyncio
async def test_upload_unsupported_and_empty_rejected(client):
    token = await _register_and_login(client, "alice@example.com")
    pid = (
        await client.post(
            "/api/v1/research", json={"question": "Fehler?"}, headers=auth_header(token)
        )
    ).json()["id"]

    # Unbekannter Typ
    resp = await client.post(
        f"/api/v1/research/{pid}/documents",
        headers=auth_header(token),
        files={"files": ("bild.png", b"\x89PNG", "image/png")},
    )
    assert resp.status_code == 422

    # "Leere" Extraktion (Scan-Simulation): txt mit nur Whitespace
    resp = await client.post(
        f"/api/v1/research/{pid}/documents",
        headers=auth_header(token),
        files={"files": ("scan.txt", b"   \n  ", "text/plain")},
    )
    assert resp.status_code == 422
    assert "Scan" in resp.json()["detail"] or "Kein Text" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_upload_to_foreign_project_404(client):
    token_a = await _register_and_login(client, "alice@example.com")
    token_b = await _register_and_login(client, "bob@example.com")
    pid = (
        await client.post(
            "/api/v1/research", json={"question": "A's Docs"}, headers=auth_header(token_a)
        )
    ).json()["id"]

    resp = await client.post(
        f"/api/v1/research/{pid}/documents",
        headers=auth_header(token_b),
        files={"files": ("x.txt", b"inhalt", "text/plain")},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_document_search_tool_finds_terms():
    tool = make_document_search_tool(
        [
            {"name": "gehaelter.txt", "text": "Laravel Entwickler verdienen 55000 Euro. FastAPI 65000 Euro."},
            {"name": "anderes.txt", "text": "Hier steht nichts Relevantes."},
        ]
    )
    result = await tool.ainvoke({"query": "Laravel Euro"})
    assert "gehaelter.txt" in result
    assert "55000" in result

    nothing = await tool.ainvoke({"query": "Quantenphysik"})
    assert "Keine Treffer" in nothing


@pytest.mark.asyncio
async def test_followup_inherits_chain_documents(client, test_session):
    """Ketten-Vererbung: Ein Doc am ELTERN-Projekt muss beim Follow-up-Lauf
    verfügbar sein (der Agent lädt kettenweit — User-Wunsch)."""
    from uuid import UUID as _UUID

    from sqlmodel import select as _select

    from app.models.research_project import ResearchProject
    from app.services.document_service import list_documents_for_chain

    token = await _register_and_login(client, "alice@example.com")
    parent = (
        await client.post(
            "/api/v1/research", json={"question": "Original mit Anhang?"}, headers=auth_header(token)
        )
    ).json()
    child = (
        await client.post(
            "/api/v1/research",
            json={"question": "Und was genau steht im Anhang?", "followup_of": parent["id"]},
            headers=auth_header(token),
        )
    ).json()

    # Doc am PARENT hochladen …
    resp = await client.post(
        f"/api/v1/research/{parent['id']}/documents",
        headers=auth_header(token),
        files={"files": ("anlage.txt", b"Geheime Zahl: 42", "text/plain")},
    )
    assert resp.status_code == 201

    # … muss beim CHILD über die Ketten-Sicht auftauchen.
    child_row = (
        await test_session.exec(
            _select(ResearchProject).where(ResearchProject.id == _UUID(child["id"]))
        )
    ).first()
    assert child_row is not None
    docs = await list_documents_for_chain(test_session, child_row)
    assert any(d.filename == "anlage.txt" for d in docs)


@pytest.mark.asyncio
async def test_delete_document(client):
    token = await _register_and_login(client, "alice@example.com")
    pid = (
        await client.post(
            "/api/v1/research", json={"question": "Löschtest?"}, headers=auth_header(token)
        )
    ).json()["id"]
    doc = (
        await client.post(
            f"/api/v1/research/{pid}/documents",
            headers=auth_header(token),
            files={"files": ("wegdamit.txt", b"inhalt", "text/plain")},
        )
    ).json()[0]

    resp = await client.delete(
        f"/api/v1/research/{pid}/documents/{doc['id']}", headers=auth_header(token)
    )
    assert resp.status_code == 204
    resp = await client.get(f"/api/v1/research/{pid}/documents", headers=auth_header(token))
    assert resp.json() == []


@pytest.mark.asyncio
async def test_list_documents_chain_param(client):
    """?chain=true: Docs des PARENTS erscheinen in der Liste des CHILDs."""
    token = await _register_and_login(client, "alice@example.com")
    parent = (
        await client.post(
            "/api/v1/research", json={"question": "Parent?"}, headers=auth_header(token)
        )
    ).json()
    child = (
        await client.post(
            "/api/v1/research",
            json={"question": "Child?", "followup_of": parent["id"]},
            headers=auth_header(token),
        )
    ).json()
    await client.post(
        f"/api/v1/research/{parent['id']}/documents",
        headers=auth_header(token),
        files={"files": ("buch2.txt", b"Inhalt Buch 2", "text/plain")},
    )

    plain = await client.get(
        f"/api/v1/research/{child['id']}/documents", headers=auth_header(token)
    )
    assert plain.json() == []  # ohne chain: nur eigene (keine)

    chained = await client.get(
        f"/api/v1/research/{child['id']}/documents?chain=true", headers=auth_header(token)
    )
    names = [d["filename"] for d in chained.json()]
    assert "buch2.txt" in names  # mit chain: erbt Parent-Dateien
