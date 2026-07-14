"""`POST /auth/token` — API_SPEC.md: "not detailed further — out of scope beyond 'exists and is
standard'." Standard shape: exchange a known API key for a short-lived JWT so a client that
*does* want the token-refresh-free path (X-API-Key) still has a way to get the other credential
type when it wants one."""

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import create_access_token, resolve_api_key
from app.core.config import Settings, get_settings
from app.core.db import get_db
from app.schemas.auth import TokenRequest, TokenResponse

router = APIRouter()


@router.post("/auth/token", response_model=TokenResponse)
async def issue_token(
    body: TokenRequest,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    identity = await resolve_api_key(body.api_key, db)
    token = create_access_token(identity.id, settings=settings)
    return TokenResponse(
        access_token=token, expires_in_seconds=settings.jwt_access_token_ttl_minutes * 60
    )
