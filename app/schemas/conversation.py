"""Response models for `GET /conversations` and `GET /conversations/:id` — API_SPEC.md.

Kept in `app/schemas/` per the API_SPEC.md intro: FastAPI's `/docs`/`/openapi.json`
are generated directly from these, so they're the actual source of truth for
the wire contract, not hand-written docs reverse-engineered from routes.
"""

from datetime import datetime
from uuid import UUID

from shared.schemas import CamelModel


class MessageOut(CamelModel):
    # UUID, not str: ORM attributes are `uuid.UUID` (from_attributes doesn't coerce
    # UUID -> str), and CamelModel/pydantic serializes UUID to a JSON string anyway.
    id: UUID
    role: str
    content: str
    model: str | None
    latency_ms: int | None
    prompt_tokens: int | None
    completion_tokens: int | None
    retry_count: int
    created_at: datetime


class ConversationOut(CamelModel):
    id: UUID
    title: str | None
    created_at: datetime
    updated_at: datetime


class ConversationDetailOut(ConversationOut):
    messages: list[MessageOut]
