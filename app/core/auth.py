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


# 【卷 四.三.一】这是一个容易被追问的设计决策："密码不是应该用 bcrypt 之类的慢哈希吗？"
# 【卷】答案：bcrypt 解决的是"低熵、人类能记住的密码"场景——密码空间小，攻击者可以离线暴力破解，
# 【卷】所以要故意让哈希变慢来拖慢暴力破解速度。但 API Key 是程序生成的高熵随机字符串
# 【卷】（见 scripts/create_user.py 里 secrets.token_urlsafe(24)），本身就没法暴力破解，
# 【卷】慢哈希在这里只有坏处没有好处：bcrypt 不支持"直接用哈希值做数据库索引查找"，
# 【卷】要验证一个 key 就得把它跟表里每一行都做一次慢比对——请求量一大就是灾难。
# 【卷】SHA-256 是确定性的快哈希，可以直接建唯一索引，O(1) 查一次就命中，这才是这个场景该用的工具。
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


# 【卷 四.三.二】这是 FastAPI 的依赖注入（Dependency Injection）用法：路由函数只要写
# 【卷】`identity: Identity = Depends(get_current_identity)`，FastAPI 会自动调用这个函数、
# 【卷】把结果注入进去，路由函数本身完全不用关心"用户是怎么登录的"。两种凭证（JWT/API Key）
# 【卷】在这里被收敛成同一个 Identity 类型返回——下游的限流、消息归属等逻辑只认 identity.id，
# 【卷】不需要知道也不关心这次请求到底是用哪种方式认证的。
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
