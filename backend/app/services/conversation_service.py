"""
services/conversation_service.py — Unterhaltungen (Chat-Modell wie Google AI Studio)
====================================================================================
Eine Conversation = ein Chat. Nachrichten (research_projects) hängen über
conversation_id drin. Die Historie listet Conversations — Nachfragen sind
Nachrichten im Chat, keine eigenen Einträge.
"""

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.models.conversation import Conversation
from app.models.research_project import ResearchProject
from app.models.user import User


class ConversationNotFound(Exception):
    """Unterhaltung existiert nicht — oder gehört einem anderen User."""


def _owned(conv: Conversation, user: User) -> bool:
    return conv.user_id == user.id


async def create_conversation(session: AsyncSession, user: User, title: str) -> Conversation:
    conv = Conversation(user_id=user.id, title=title[:200])
    session.add(conv)
    await session.commit()
    await session.refresh(conv)
    return conv


async def list_conversations(session: AsyncSession, user: User) -> list[dict]:
    """Alle Unterhaltungen des Users (neueste zuerst) mit Nachrichtenzahl."""
    result = await session.exec(
        select(Conversation)
        .where(Conversation.user_id == user.id)
        .order_by(Conversation.created_at.desc())
    )
    conversations = list(result.all())
    out: list[dict] = []
    for conv in conversations:
        projects = await session.exec(
            select(ResearchProject)
            .where(ResearchProject.conversation_id == conv.id)
            .order_by(ResearchProject.created_at)
        )
        msgs = list(projects.all())
        out.append(
            {
                "id": conv.id,
                "title": conv.title,
                "created_at": conv.created_at,
                "updated_at": conv.updated_at,
                "message_count": len(msgs),
                "total_tokens": sum((m.usage or {}).get("total_tokens", 0) for m in msgs),
                "last_question": msgs[-1].question if msgs else None,
            }
        )
    return out


async def get_conversation_projects(
    session: AsyncSession, user: User, conversation_id: UUID
) -> list[ResearchProject]:
    """Alle Nachrichten einer Unterhaltung (chronologisch)."""
    conv = await session.get(Conversation, conversation_id)
    if conv is None or not _owned(conv, user):
        raise ConversationNotFound(str(conversation_id))
    result = await session.exec(
        select(ResearchProject)
        .where(ResearchProject.conversation_id == conversation_id)
        .order_by(ResearchProject.created_at)
    )
    return list(result.all())


async def rename_conversation(
    session: AsyncSession, user: User, conversation_id: UUID, title: str
) -> Conversation:
    conv = await session.get(Conversation, conversation_id)
    if conv is None or not _owned(conv, user):
        raise ConversationNotFound(str(conversation_id))
    conv.title = title.strip()[:200]
    session.add(conv)
    await session.commit()
    await session.refresh(conv)
    return conv


async def delete_conversation(session: AsyncSession, user: User, conversation_id: UUID) -> None:
    """Löscht eine Unterhaltung inkl. ALLER Nachrichten (research_projects)
    und deren Dokumente — beides per DB-CASCADE."""
    conv = await session.get(Conversation, conversation_id)
    if conv is None or not _owned(conv, user):
        raise ConversationNotFound(str(conversation_id))

    # Explizit löschen (statt auf DB-CASCADE zu vertrauen): SQLite (Tests)
    # erzwingt FK-Cascades nicht — so ist es auf JEDER DB deterministisch.
    from app.models.document import Document

    projects = list(
        (
            await session.exec(
                select(ResearchProject).where(ResearchProject.conversation_id == conversation_id)
            )
        ).all()
    )
    for project in projects:
        docs = list(
            (
                await session.exec(
                    select(Document).where(Document.project_id == project.id)
                )
            ).all()
        )
        for doc in docs:
            await session.delete(doc)
        await session.delete(project)
    await session.delete(conv)
    await session.commit()


async def conversation_for_project(
    session: AsyncSession, project: ResearchProject
) -> Conversation | None:
    if project.conversation_id is None:
        return None
    return await session.get(Conversation, project.conversation_id)
