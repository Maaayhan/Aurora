"""JWT + API key auth dependency, identity resolution — API_SPEC.md Auth, ARCHITECTURE.md §6.

Both credential types resolve to the same `identity_id` used everywhere
downstream (rate limiting, message attribution). This module answers "who is
calling" only — it is explicitly not responsible for rate limit decisions
(`app/core/rate_limit.py` owns that).
"""

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import jwt
from fastapi import Depends, Header
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.db import get_db
from app.core.models import User
from shared.errors import AuroraError, ErrorCode


@dataclass(frozen=True)
class Identity:
    id: str


def create_access_token(user_id: str, *, settings: Settings | None = None) -> str:
    settings = settings or get_settings()
    now = datetime.now(UTC)
    claims = {
        "sub": user_id,
        "iat": now,
        "exp": now + timedelta(minutes=settings.jwt_access_token_ttl_minutes),
    }
    return jwt.encode(claims, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def _decode_access_token(token: str, settings: Settings) -> str:
    try:
        claims = jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
    except jwt.PyJWTError as exc:
        raise AuroraError(ErrorCode.UNAUTHENTICATED, "Invalid or expired token") from exc
    subject = claims.get("sub")
    if not subject:
        raise AuroraError(ErrorCode.UNAUTHENTICATED, "Token missing subject claim")
    return subject


def hash_api_key(raw_key: str) -> str:
    """SHA-256, not bcrypt: API keys are high-entropy random tokens (unlike passwords), so a
    fast deterministic hash is the right tradeoff — it lets `api_key_hash` stay a unique-indexed
    lookup column (O(1) query) instead of requiring a linear bcrypt-compare scan over every user
    on each request. This is the same approach Stripe/GitHub-style API key auth uses."""
    return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()


async def resolve_api_key(raw_key: str, db: AsyncSession) -> Identity:
    key_hash = hash_api_key(raw_key)
    result = await db.execute(select(User).where(User.api_key_hash == key_hash))
    user = result.scalar_one_or_none()
    if user is None:
        raise AuroraError(ErrorCode.UNAUTHENTICATED, "Unknown API key")
    return Identity(id=str(user.id))


async def get_current_identity(
    authorization: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> Identity:
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization[len("bearer ") :].strip()
        user_id = _decode_access_token(token, settings)
        return Identity(id=user_id)

    if x_api_key:
        return await resolve_api_key(x_api_key, db)

    raise AuroraError(ErrorCode.UNAUTHENTICATED, "No credential supplied")
