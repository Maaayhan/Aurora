"""`POST /messages/:id/feedback` — API_SPEC.md."""

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import Identity, get_current_identity
from app.core.db import get_db
from app.schemas.feedback import FeedbackIn, FeedbackOut
from app.services import session_service

router = APIRouter()


@router.post("/messages/{message_id}/feedback", response_model=FeedbackOut)
async def submit_feedback(
    message_id: str,
    body: FeedbackIn,
    identity: Identity = Depends(get_current_identity),
    db: AsyncSession = Depends(get_db),
):
    feedback = await session_service.add_feedback(db, message_id, identity.id, body.rating)
    return FeedbackOut.model_validate(feedback, from_attributes=True)
