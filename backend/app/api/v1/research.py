"""
api/v1/research.py — Recherche-Endpunkte (/api/v1/research/*)
==============================================================

DÜNNE Endpunkte (Muster wie auth.py/users.py): HTTP-Dinge + Delegation an
research_service. Alle Endpunkte verlangen Login (CurrentUserDep) — ohne
Auth gibt es nicht mal eine Liste.

Der spannende Endpunkt ist POST /{id}/run: Er liefert KEIN JSON, sondern
einen Server-Sent-Events-Strom (text/event-stream) — die Live-Ausgabe des
LangGraph-Laufs (siehe PLAN.md §4 für das Event-Protokoll).
"""

import logging
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, File, HTTPException, Response, UploadFile, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.core.db import SessionDep
from app.core.deps import CurrentUserDep, RequireAdmin
from app.schemas.research import (
    ResearchAdminOut,
    ResearchCreate,
    ResearchProjectOut,
    ResearchUpdate,
)
from app.services import research_service
from app.services.research_service import ProjectNotFound

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/research", tags=["research"])


# ----------------------------------------------------------------------------
# GET /research/admin/all — ALLE Recherchen (NUR ADMIN, Stufe 5)
# ----------------------------------------------------------------------------
@router.get("/admin/all", response_model=list[ResearchAdminOut], dependencies=[RequireAdmin])
async def list_all_research(session: SessionDep):
    """
    Admin-Sicht: alle Recherchen aller User inkl. E-Mail und Token-Verbrauch.
    WICHTIG: Muss VOR /{project_id} registriert sein, sonst frisst der
    Pfad-Parameter "admin" die Route!
    """
    return await research_service.list_all_projects(session)


# ----------------------------------------------------------------------------
# POST /research — neue Recherche anlegen
# ----------------------------------------------------------------------------
@router.post("", response_model=ResearchProjectOut, status_code=status.HTTP_201_CREATED)
async def create_research(
    data: ResearchCreate,
    current_user: CurrentUserDep,
    session: SessionDep,
):
    """Legt eine neue Recherche an (Status: queued). Der Graph startet erst mit /run."""
    try:
        return await research_service.create_project(session, current_user, data)
    except research_service.ProjectNotFound:
        # followup_of zeigt auf ein fremdes Projekt -> nicht verraten, 404.
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Recherche nicht gefunden.",
        ) from None


# ----------------------------------------------------------------------------
# GET /research — eigene Recherchen listen
# ----------------------------------------------------------------------------
@router.get("", response_model=list[ResearchProjectOut])
async def list_research(
    current_user: CurrentUserDep,
    session: SessionDep,
):
    """Alle Recherche-Projekte des eingeloggten Users (neueste zuerst)."""
    return await research_service.list_projects(session, current_user)


# ----------------------------------------------------------------------------
# GET /research/{id} — Detail (inkl. fertigem Report)
# ----------------------------------------------------------------------------
@router.get("/{project_id}", response_model=ResearchProjectOut)
async def get_research(
    project_id: UUID,
    current_user: CurrentUserDep,
    session: SessionDep,
):
    """
    Einzelnes Projekt. Gehört es einem anderen User, liefern wir 404 (nicht
    403) — so verraten wir nicht einmal, DASS es das Projekt gibt.
    """
    try:
        return await research_service.get_project(session, current_user, project_id)
    except ProjectNotFound:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Recherche nicht gefunden.",
        ) from None


# ----------------------------------------------------------------------------
# PATCH /research/{id} — Unterhaltung umbenennen (steuert auch den PDF-Titel)
# ----------------------------------------------------------------------------
@router.patch("/{project_id}", response_model=ResearchProjectOut)
async def rename_research(
    project_id: UUID,
    data: ResearchUpdate,
    current_user: CurrentUserDep,
    session: SessionDep,
):
    """Setzt den Anzeigename. Die Originalfrage bleibt als Kontext erhalten."""
    try:
        return await research_service.rename_project(session, current_user, project_id, data.title)
    except ProjectNotFound:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Recherche nicht gefunden.",
        ) from None


# ----------------------------------------------------------------------------
# DELETE /research/{id} — Projekt löschen
# ----------------------------------------------------------------------------
@router.delete("/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_research(
    project_id: UUID,
    current_user: CurrentUserDep,
    session: SessionDep,
):
    """Löscht eine Recherche (Ownership-Check wie bei GET)."""
    try:
        await research_service.delete_project(session, current_user, project_id)
    except ProjectNotFound:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Recherche nicht gefunden.",
        ) from None


# ----------------------------------------------------------------------------
# GET /research/{id}/pdf — Report als echten PDF-DOWNLOAD
# ----------------------------------------------------------------------------
# KEIN window.print()-Trick: Der Server rendert Markdown → HTML → PDF und
# liefert die Datei mit Content-Disposition: attachment — der Browser
# speichert sie direkt als .pdf.
@router.get("/{project_id}/pdf")
async def download_research_pdf(
    project_id: UUID,
    current_user: CurrentUserDep,
    session: SessionDep,
):
    """Report + Quellen als PDF-Datei (Download). 409, falls kein Report da."""
    try:
        project = await research_service.get_project(session, current_user, project_id)
    except ProjectNotFound:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Recherche nicht gefunden.",
        ) from None

    try:
        pdf_bytes = research_service.report_to_pdf(project)
    except ValueError as e:
        # Kein Report → 409 Conflict (Ressource existiert, ist aber nicht bereit).
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(e),
        ) from None

    # Dateiname: Frage gekürzt — RFC 5987-kodiert für Umlaute (filename*),
    # plus ASCII-Fallback (filename).
    import re as _re
    from urllib.parse import quote as _quote

    slug = (
        _re.sub(r"[^a-zA-Z0-9ÄÖÜäöüß -]", "", project.title or project.question)[:40]
        .strip()
        or "report"
    )
    ascii_slug = slug.encode("ascii", "ignore").decode().strip() or "report"

    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": (
                f'attachment; filename="deep-research-{ascii_slug}.pdf"; '
                f"filename*=UTF-8''{_quote(f'deep-research-{slug}.pdf')}"
            ),
        },
    )


# ----------------------------------------------------------------------------
# DOKUMENTE — Upload (PDF/DOCX/TXT/MD/CSV), Liste, Löschen
# ----------------------------------------------------------------------------
class DocumentOut(BaseModel):
    """Ausgabe eines hochgeladenen Dokuments (ohne den Volltext)."""

    id: UUID
    project_id: UUID  # Eigentümer-Projekt (bei Ketten-Sicht relevant fürs Löschen)
    filename: str
    mime_type: str
    size_bytes: int
    char_count: int


@router.post(
    "/{project_id}/documents",
    response_model=list[DocumentOut],
    status_code=status.HTTP_201_CREATED,
)
async def upload_documents(
    project_id: UUID,
    current_user: CurrentUserDep,
    session: SessionDep,
    files: Annotated[list[UploadFile], File(description="Eine oder mehrere Dateien")],
):
    """Lädt Dokumente hoch und extrahiert sofort den Text (OCR nicht unterstützt)."""
    try:
        project = await research_service.get_project(session, current_user, project_id)
    except ProjectNotFound:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Recherche nicht gefunden.",
        ) from None

    from app.services import document_service

    created = []
    for f in files:
        try:
            created.append(await document_service.upload_document(session, project, f))
        except document_service.DocumentError as e:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e)
            ) from None
    return created


@router.get("/{project_id}/documents", response_model=list[DocumentOut])
async def list_documents_endpoint(
    project_id: UUID,
    current_user: CurrentUserDep,
    session: SessionDep,
    chain: bool = False,
):
    """Dokumente einer Recherche — mit ?chain=true die GANZE Unterhaltung
    (auch Dateien, die an Follow-up-Fragen dieses Chats hochgeladen wurden)."""
    try:
        project = await research_service.get_project(session, current_user, project_id)
    except ProjectNotFound:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Recherche nicht gefunden.",
        ) from None

    from app.services import document_service

    if chain:
        return await document_service.list_documents_for_chain(session, project)
    return await document_service.list_documents(session, project)


@router.delete("/{project_id}/documents/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_document_endpoint(
    project_id: UUID,
    document_id: UUID,
    current_user: CurrentUserDep,
    session: SessionDep,
):
    """Entfernt ein Dokument aus der Recherche (wirkt ab dem NÄCHSTEN Lauf)."""
    try:
        project = await research_service.get_project(session, current_user, project_id)
    except ProjectNotFound:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Recherche nicht gefunden.",
        ) from None

    from app.services import document_service

    try:
        await document_service.delete_document(session, project, document_id)
    except document_service.DocumentError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from None


# ----------------------------------------------------------------------------
# POST /research/{id}/resume — STAGE 3: Graph nach Freigabe fortsetzen
# ----------------------------------------------------------------------------
class OutlineApproval(BaseModel):
    """POST /research/{id}/resume — die (evtl. bearbeitete) Gliederung."""

    outline: dict


@router.post("/{project_id}/resume")
async def resume_research(
    project_id: UUID,
    approval: OutlineApproval,
    current_user: CurrentUserDep,
    session: SessionDep,
):
    """
    Setzt einen pausierten Deep-Report fort (nach Outline-Freigabe).
    Der Body enthält die ggf. bearbeitete Gliederung.
    Liefert SSE-Stream (wie /run).
    """
    try:
        project = await research_service.get_project(session, current_user, project_id)
    except ProjectNotFound:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Recherche nicht gefunden.",
        ) from None

    return StreamingResponse(
        research_service.resume_research_stream(session, project, approval.outline),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ----------------------------------------------------------------------------
# POST /research/{id}/run — DER SSE-ENDPUNKT: Graph live streamen
# ----------------------------------------------------------------------------
@router.post("/{project_id}/run")
async def run_research(
    project_id: UUID,
    current_user: CurrentUserDep,
    session: SessionDep,
):
    """
    Führt den LangGraph-Lauf für das Projekt aus und streamt die Events:

        event: status  {"status": "running"}
        event: token   {"text": "..."}          # Live-Token der Antwort
        event: done    {"status": "done"}
        event: error   {"detail": "..."}

    Warum POST + fetch-Streaming statt EventSource? EventSource kann nur GET
    und keine Auth-Header senden — fetch + ReadableStream kann beides.

    Die Header verhindern Pufferung durch Proxys (Vite-Dev-Proxy, nginx):
    ohne `X-Accel-Buffering: no` würden Token-Chunks gebündelt und der
    Live-Effekt wäre weg.
    """
    try:
        project = await research_service.get_project(session, current_user, project_id)
    except ProjectNotFound:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Recherche nicht gefunden.",
        ) from None

    return StreamingResponse(
        research_service.run_research_stream(session, project),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
