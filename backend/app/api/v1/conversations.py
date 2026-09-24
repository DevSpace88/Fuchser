"""
api/v1/conversations.py — Unterhaltungs-Endpunkte (/api/v1/conversations/*)
=============================================================================
Die Historie des Chats: listet CONVERSATIONS (nicht einzelne Fragen).
Nachfragen sind Nachrichten innerhalb einer Unterhaltung.
"""

from uuid import UUID

from fastapi import APIRouter, HTTPException, status

from app.core.db import SessionDep
from app.core.deps import CurrentUserDep
from app.schemas.research import (
    ConversationDetail,
    ConversationOut,
    ConversationUpdate,
    ResearchProjectOut,
)
from app.services import conversation_service
from app.services.conversation_service import ConversationNotFound

router = APIRouter(prefix="/conversations", tags=["conversations"])


@router.get("", response_model=list[ConversationOut])
async def list_conversations(current_user: CurrentUserDep, session: SessionDep):
    """Alle Unterhaltungen des Users (neueste zuerst)."""
    return await conversation_service.list_conversations(session, current_user)


@router.get("/{conversation_id}", response_model=ConversationDetail)
async def get_conversation(
    conversation_id: UUID,
    current_user: CurrentUserDep,
    session: SessionDep,
):
    """Eine Unterhaltung inkl. aller Nachrichten (chronologisch)."""
    try:
        projects = await conversation_service.get_conversation_projects(
            session, current_user, conversation_id
        )
    except ConversationNotFound:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Unterhaltung nicht gefunden.",
        ) from None
    from app.models.conversation import Conversation

    conv_row = await session.get(Conversation, conversation_id)
    base = await conversation_service.list_conversations(session, current_user)
    meta = next((c for c in base if c["id"] == conversation_id), None)
    if meta is None or conv_row is None:
        raise HTTPException(status_code=404, detail="Unterhaltung nicht gefunden.")
    return {
        **meta,
        "messages": [ResearchProjectOut.model_validate(p, from_attributes=True) for p in projects],
    }


@router.delete("/{conversation_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_conversation_endpoint(
    conversation_id: UUID,
    current_user: CurrentUserDep,
    session: SessionDep,
):
    """Löscht eine Unterhaltung inkl. aller Nachrichten und Dokumente."""
    try:
        await conversation_service.delete_conversation(session, current_user, conversation_id)
    except ConversationNotFound:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Unterhaltung nicht gefunden.",
        ) from None


@router.patch("/{conversation_id}", response_model=ConversationOut)
async def rename_conversation(
    conversation_id: UUID,
    data: ConversationUpdate,
    current_user: CurrentUserDep,
    session: SessionDep,
):
    """Unterhaltung umbenennen."""
    try:
        conv = await conversation_service.rename_conversation(
            session, current_user, conversation_id, data.title
        )
    except ConversationNotFound:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Unterhaltung nicht gefunden.",
        ) from None
    return conv
