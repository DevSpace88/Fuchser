"""
api/v1/research.py — research endpoints (/api/v1/research/*)
=============================================================

THIN endpoints (pattern like auth.py/users.py): HTTP concerns + delegation
to research_service. All endpoints require login (CurrentUserDep) — without
auth you do not even get a list.

The exciting endpoint is POST /{id}/run: it returns NO JSON but a
Server-Sent-Events stream (text/event-stream) — the live output of the
LangGraph run (see PLAN.md §4 for the event protocol).
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
# GET /research/admin/all — ALL research (ADMIN ONLY, stage 5)
# ----------------------------------------------------------------------------
@router.get("/admin/all", response_model=list[ResearchAdminOut], dependencies=[RequireAdmin])
async def list_all_research(session: SessionDep):
    """
    Admin view: all research of all users incl. email and token usage.
    IMPORTANT: must be registered BEFORE /{project_id}, otherwise the
    path parameter "admin" swallows the route!
    """
    return await research_service.list_all_projects(session)


# ----------------------------------------------------------------------------
# POST /research — create a new research run
# ----------------------------------------------------------------------------
@router.post("", response_model=ResearchProjectOut, status_code=status.HTTP_201_CREATED)
async def create_research(
    data: ResearchCreate,
    current_user: CurrentUserDep,
    session: SessionDep,
):
    """Creates a new research run (status: queued). The graph only starts with /run."""
    try:
        return await research_service.create_project(session, current_user, data)
    except research_service.ProjectNotFound:
        # followup_of points to someone else's project -> do not reveal it, 404.
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Recherche nicht gefunden.",
        ) from None


# ----------------------------------------------------------------------------
# GET /research — list your own research
# ----------------------------------------------------------------------------
@router.get("", response_model=list[ResearchProjectOut])
async def list_research(
    current_user: CurrentUserDep,
    session: SessionDep,
):
    """All research projects of the logged-in user (newest first)."""
    return await research_service.list_projects(session, current_user)


# ----------------------------------------------------------------------------
# GET /research/{id} — detail (incl. finished report)
# ----------------------------------------------------------------------------
@router.get("/{project_id}", response_model=ResearchProjectOut)
async def get_research(
    project_id: UUID,
    current_user: CurrentUserDep,
    session: SessionDep,
):
    """
    Single project. If it belongs to another user, we return 404 (not
    403) — this way we do not even reveal THAT the project exists.
    """
    try:
        return await research_service.get_project(session, current_user, project_id)
    except ProjectNotFound:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Recherche nicht gefunden.",
        ) from None


# ----------------------------------------------------------------------------
# PATCH /research/{id} — rename the conversation (also drives the PDF title)
# ----------------------------------------------------------------------------
@router.patch("/{project_id}", response_model=ResearchProjectOut)
async def rename_research(
    project_id: UUID,
    data: ResearchUpdate,
    current_user: CurrentUserDep,
    session: SessionDep,
):
    """Sets the display name. The original question is kept as context."""
    try:
        return await research_service.rename_project(session, current_user, project_id, data.title)
    except ProjectNotFound:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Recherche nicht gefunden.",
        ) from None


# ----------------------------------------------------------------------------
# DELETE /research/{id} — delete project
# ----------------------------------------------------------------------------
@router.delete("/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_research(
    project_id: UUID,
    current_user: CurrentUserDep,
    session: SessionDep,
):
    """Deletes a research run (ownership check as with GET)."""
    try:
        await research_service.delete_project(session, current_user, project_id)
    except ProjectNotFound:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Recherche nicht gefunden.",
        ) from None


# ----------------------------------------------------------------------------
# GET /research/{id}/pdf — report as a real PDF DOWNLOAD
# ----------------------------------------------------------------------------
# NO window.print() trick: the server renders Markdown → HTML → PDF and
# delivers the file with Content-Disposition: attachment — the browser
# saves it directly as .pdf.
@router.get("/{project_id}/pdf")
async def download_research_pdf(
    project_id: UUID,
    current_user: CurrentUserDep,
    session: SessionDep,
):
    """Report + sources as a PDF file (download). 409 if no report exists yet."""
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
        # No report → 409 Conflict (resource exists but is not ready).
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(e),
        ) from None

    # File name: shortened question — RFC 5987-encoded for umlauts (filename*),
    # plus an ASCII fallback (filename).
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
# DOCUMENTS — upload (PDF/DOCX/TXT/MD/CSV), list, delete
# ----------------------------------------------------------------------------
class DocumentOut(BaseModel):
    """Output of an uploaded document (without the full text)."""

    id: UUID
    project_id: UUID  # owning project (relevant for deletion in the chain view)
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
    """Uploads documents and immediately extracts the text (OCR not supported)."""
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
    """Documents of a research run — with ?chain=true the ENTIRE conversation
    (including files uploaded to follow-up questions of this chat)."""
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
    """Removes a document from the research run (takes effect from the NEXT run)."""
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
# POST /research/{id}/resume — STAGE 3: continue the graph after approval
# ----------------------------------------------------------------------------
class OutlineApproval(BaseModel):
    """POST /research/{id}/resume — the (possibly edited) outline."""

    outline: dict


@router.post("/{project_id}/resume")
async def resume_research(
    project_id: UUID,
    approval: OutlineApproval,
    current_user: CurrentUserDep,
    session: SessionDep,
):
    """
    Continues a paused deep report (after outline approval).
    The body contains the possibly edited outline.
    Returns an SSE stream (like /run).
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
# POST /research/{id}/run — THE SSE ENDPOINT: stream the graph live
# ----------------------------------------------------------------------------
@router.post("/{project_id}/run")
async def run_research(
    project_id: UUID,
    current_user: CurrentUserDep,
    session: SessionDep,
):
    """
    Executes the LangGraph run for the project and streams the events:

        event: status  {"status": "running"}
        event: token   {"text": "..."}          # live tokens of the answer
        event: done    {"status": "done"}
        event: error   {"detail": "..."}

    Why POST + fetch streaming instead of EventSource? EventSource can only
    do GET and cannot send auth headers — fetch + ReadableStream can do both.

    The headers prevent buffering by proxies (Vite dev proxy, nginx):
    without `X-Accel-Buffering: no` token chunks would be batched and the
    live effect would be gone.
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
