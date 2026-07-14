"""`GET /health` — API_SPEC.md.

`degraded` = inference layer unhealthy but the API can still accept/queue
requests; `unhealthy` = Postgres or Redis unreachable, i.e. the gateway
cannot function at all (ERROR_HANDLING.md §7) — that distinction is what a
load balancer needs to decide "stop routing here" vs. "this instance just
can't serve inference right now."

This is the one place in `app/` that imports a `providers/` class directly —
an intentional, narrow exception to the "app/ never imports a provider class"
rule (ARCHITECTURE.md §7), because pinging the model provider is explicitly
this module's documented job (ARCHITECTURE.md §6 module table).
"""

# 【卷 七.三】三.一 讲过 ModelProvider 是"worker/ 唯一允许依赖的模型接口"，这个文件是全项目
# 【卷】唯一的例外——app/api/health.py 直接 import 了 providers/。为什么这里可以破例？
# 【卷】因为"给模型服务发一个轻量探活请求"本来就是 /health 端点明确的职责（API_SPEC.md），
# 【卷】跟"worker/ 不该关心 HTTP"、"app/ 不该跑推理逻辑"这两条边界规则并不冲突——这里只是
# 【卷】问一句"你还活着吗"，不是真的在做推理调用。其余几个路由文件（auth.py/conversations.py/
# 【卷】feedback.py）逻辑都比较简单，扫一遍即可，不需要在这几个文件上花太多时间。
import time
from functools import lru_cache
from typing import Literal

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.db import get_db
from app.core.redis import get_broker_redis, get_redis
from providers.base import ModelProvider
from providers.factory import build_provider

router = APIRouter()


@lru_cache
def _health_provider() -> ModelProvider:
    # No-arg + lru_cache rather than parametrized by `Settings`: pydantic-settings models
    # aren't hashable, and `get_settings()` is already itself a cached singleton.
    settings = get_settings()
    return build_provider(
        settings.model_provider,
        provider_url=settings.model_provider_url,
        default_model=settings.default_model,
        timeout_seconds=5.0,
    )


async def _check_postgres(db: AsyncSession) -> dict:
    start = time.monotonic()
    try:
        await db.execute(text("SELECT 1"))
        return {"healthy": True, "latencyMs": round((time.monotonic() - start) * 1000, 2)}
    except Exception as exc:  # noqa: BLE001 — any DB failure means "unhealthy", detail isn't API contract
        return {"healthy": False, "latencyMs": None, "error": str(exc)}


async def _check_redis() -> dict:
    start = time.monotonic()
    try:
        await get_redis().ping()
        return {"healthy": True, "latencyMs": round((time.monotonic() - start) * 1000, 2)}
    except Exception as exc:  # noqa: BLE001
        return {"healthy": False, "latencyMs": None, "error": str(exc)}


async def _check_queue() -> dict:
    try:
        depth = await get_broker_redis().llen("celery")  # type: ignore[misc] # redis-py stub quirk
        return {"healthy": True, "depth": depth}
    except Exception as exc:  # noqa: BLE001
        return {"healthy": False, "depth": None, "error": str(exc)}


async def _check_inference() -> dict:
    result = await _health_provider().health_check()
    return {"healthy": result.healthy, "latencyMs": result.latency_ms, "error": result.error}


@router.get("/health")
async def health(db: AsyncSession = Depends(get_db)):
    postgres_check = await _check_postgres(db)
    redis_check = await _check_redis()
    queue_check = await _check_queue()
    inference_check = await _check_inference()

    overall: Literal["healthy", "degraded", "unhealthy"]
    if not postgres_check["healthy"] or not redis_check["healthy"]:
        overall = "unhealthy"
    elif not inference_check["healthy"]:
        overall = "degraded"
    else:
        overall = "healthy"

    return {
        "status": overall,
        "checks": {
            "postgres": postgres_check,
            "redis": redis_check,
            "queue": queue_check,
            "inference": inference_check,
        },
    }
