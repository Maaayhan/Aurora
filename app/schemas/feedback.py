"""`POST /messages/:id/feedback` request/response — API_SPEC.md."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from shared.schemas import CamelModel


class FeedbackIn(CamelModel):
    rating: Literal[-1, 1]


class FeedbackOut(CamelModel):
    id: UUID
    message_id: UUID
    rating: int
    created_at: datetime
