"""
services/document_service.py — Upload + Text-Extraktion für Dokumente
=======================================================================

Unterstützt (bewusst OHNE OCR — Scan-PDFs werden ehrlich abgelehnt
statt schlecht gebaut; siehe PLAN/Diskussion):
    * PDF  (pypdf)          — textbasierte PDFs, seitenweise
    * DOCX (python-docx)    — Absätze + Tabellenzellen
    * TXT / MD / CSV        — direkt dekodiert

Die Extraktion ist bewusst tolerant: Ein Fehler auf Seite 42 soll nicht
das ganze Dokument killen — defekte Seiten werden übersprungen und gezählt.
"""

import io
import logging
import uuid

from fastapi import UploadFile
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.models.document import MAX_TEXT_CHARS, Document
from app.models.research_project import ResearchProject

logger = logging.getLogger(__name__)

# Upload-Limit: 50 MB — EPUB-konvertierte PDFs bringen gern Image-Ballast
# mit (30 MB+ bei gut markierbarem Text). Der EXTRAHIERTE Text ist eh auf
# 1 MB Zeichen gekappt, die Rohdaten landen nie in der DB.
MAX_UPLOAD_BYTES = 50 * 1024 * 1024

ALLOWED_EXTENSIONS = {".pdf", ".docx", ".txt", ".md", ".csv"}


class DocumentError(Exception):
    """Fehler beim Upload/Extraktion (Endpunkt übersetzt in 4xx)."""


# ============================================================================
# 1) TEXT-EXTRACTION je Dateityp
# ============================================================================
def _extract_pdf(data: bytes) -> str:
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(data))
    pages: list[str] = []
    broken = 0
    for i, page in enumerate(reader.pages):
        try:
            pages.append(page.extract_text() or "")
        except Exception:  # noqa: BLE001 — einzelne defekte Seite überspringen
            broken += 1
            logger.info("PDF-Seite %d nicht extrahierbar", i + 1)
    if broken:
        logger.warning("PDF: %d/%d Seiten übersprungen", broken, len(reader.pages))
    return "\n\n".join(pages)


def _extract_docx(data: bytes) -> str:
    import docx

    doc = docx.Document(io.BytesIO(data))
    parts = [p.text for p in doc.paragraphs if p.text.strip()]
    for table in doc.tables:  # Tabellen flach ausbreiten (Zellen als Zeilen)
        for row in table.rows:
            cells = [c.text.strip() for c in row.cells if c.text.strip()]
            if cells:
                parts.append(" | ".join(cells))
    return "\n".join(parts)


def _extract_plain(data: bytes) -> str:
    # utf-8 first, ältere deutsche Dateien sind gern latin-1
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("latin-1", errors="replace")


def extract_text(filename: str, data: bytes) -> str:
    """Extrahiert Text je Endung und kappt auf MAX_TEXT_CHARS."""
    ext = "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    try:
        if ext == ".pdf":
            text = _extract_pdf(data)
        elif ext == ".docx":
            text = _extract_docx(data)
        elif ext in (".txt", ".md", ".csv"):
            text = _extract_plain(data)
        else:
            raise DocumentError(
                f"Dateityp nicht unterstützt: {ext} (erlaubt: PDF, DOCX, TXT, MD, CSV)"
            )
    except DocumentError:
        raise
    except Exception as e:  # noqa: BLE001 — Parser-Crash -> saubere 422 mit Grund
        raise DocumentError(
            f"Datei konnte nicht gelesen werden ({type(e).__name__}): {e}"
        ) from e

    if not text.strip():
        # Typisch: Scan-PDF ohne Textschicht -> ehrlich sein statt 0 Zeichen
        raise DocumentError(
            "Kein Text gefunden — handelt es sich um einen Scan? "
            "OCR (texterkennung) wird nicht unterstützt."
        )
    return text[:MAX_TEXT_CHARS]


# ============================================================================
# 2) CRUD (immer ownership-gefiltert über get_project)
# ============================================================================
async def upload_document(
    session: AsyncSession,
    project: ResearchProject,
    upload: UploadFile,
) -> Document:
    """Liest die Datei, extrahiert Text und speichert das Document."""
    data = await upload.read()
    if len(data) > MAX_UPLOAD_BYTES:
        raise DocumentError(f"Datei zu groß ({len(data) // (1024 * 1024)} MB, max. 50 MB).")
    if not data:
        raise DocumentError("Leere Datei.")

    filename = upload.filename or "datei"
    text = extract_text(filename, data)

    document = Document(
        project_id=project.id,
        filename=filename,
        mime_type=upload.content_type or "application/octet-stream",
        size_bytes=len(data),
        char_count=len(text),
        extracted_text=text,
    )
    session.add(document)
    await session.commit()
    await session.refresh(document)
    return document


async def list_documents(session: AsyncSession, project: ResearchProject) -> list[Document]:
    result = await session.exec(
        select(Document).where(Document.project_id == project.id).order_by(Document.created_at)
    )
    return list(result.all())


async def delete_document(
    session: AsyncSession, project: ResearchProject, document_id: uuid.UUID  # type: ignore[name-defined]
) -> None:
    result = await session.exec(
        select(Document).where(Document.project_id == project.id, Document.id == document_id)
    )
    document = result.first()
    if document is None:
        raise DocumentError("Dokument nicht gefunden.")
    await session.delete(document)
    await session.commit()


async def list_documents_for_chain(
    session: AsyncSession, project: ResearchProject
) -> list[Document]:
    """
    Dokumente der GESAMTEN Unterhaltung: des Projekts plus aller Vorfahren
    (parent_id-Kette). So bleiben hochgeladene Dateien über Follow-up-Fragen
    hinweg verfügbar — der Chat „verliert" sie nie (User-Wunsch).
    """
    all_docs: list[Document] = []
    seen_projects: set = set()
    current: ResearchProject | None = project
    while current is not None and current.id not in seen_projects:
        seen_projects.add(current.id)
        all_docs.extend(await list_documents(session, current))
        current = (
            await session.get(ResearchProject, current.parent_id)
            if current.parent_id
            else None
        )
    # Älteste zuerst (Reihenfolge = Upload-Chronologie der Unterhaltung)
    all_docs.sort(key=lambda d: d.created_at)
    return all_docs


def documents_for_agent(session_documents: list[Document]) -> list[dict]:
    """Kompakte Form für den Graph-State (Name + gekappter Text)."""
    return [
        {"name": d.filename, "text": d.extracted_text[:60_000]}
        for d in session_documents
    ]
