"""`GET /conversations`, `GET /conversations/:id` — API_SPEC.md."""

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import Identity, get_current_identity
from app.core.db import get_db
from app.schemas.conversation import ConversationDetailOut, ConversationOut, MessageOut
from app.services import session_service

router = APIRouter()


@router.get("/conversations", response_model=list[ConversationOut])
async def list_conversations(
    limit: int = Query(default=20, le=100),
    cursor: str | None = Query(default=None),
    identity: Identity = Depends(get_current_identity),
    db: AsyncSession = Depends(get_db),
):
    conversations = await session_service.list_conversations(
        db, identity.id, limit=limit, cursor=cursor
    )
    return [ConversationOut.model_validate(c, from_attributes=True) for c in conversations]


@router.get("/conversations/{conversation_id}", response_model=ConversationDetailOut)
async def get_conversation(
    conversation_id: str,
    limit: int = Query(default=50, le=200),
    cursor: str | None = Query(default=None),
    identity: Identity = Depends(get_current_identity),
    db: AsyncSession = Depends(get_db),
):
    conversation = await session_service.get_owned_conversation(db, conversation_id, identity.id)
    messages = await session_service.list_messages(
        db, conversation_id, limit=limit, cursor=cursor
    )
    return ConversationDetailOut(
        id=conversation.id,
        title=conversation.title,
        created_at=conversation.created_at,
        updated_at=conversation.updated_at,
        messages=[MessageOut.model_validate(m, from_attributes=True) for m in messages],
    )
