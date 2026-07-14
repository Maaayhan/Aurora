"""FastAPI application assembly — DEPLOYMENT.md §4 (structured logging), API_SPEC.md (routes).

Every request gets a `request_id` (echoed as `X-Request-Id`) bound into the
structlog context for the lifetime of the request, and one structured
completion log line with method/path/status/duration — so a request's outcome
is always in the logs even without a tracing backend (Phase 3 adds that; this
manual version is the honest fallback, not a stopgap — DEPLOYMENT.md §4).
"""

import time
import uuid

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response

from app.api import auth, chat, conversations, feedback, health
from app.core.config import get_settings
from app.core.logging import bind_request_id, clear_request_context, configure_logging, get_logger
from app.core.metrics import render_latest
from shared.errors import HTTP_STATUS_BY_CODE, AuroraError, ErrorCode

settings = get_settings()
configure_logging(settings.log_level)
logger = get_logger(__name__)

app = FastAPI(title="Aurora", version="0.1.0")

app.include_router(auth.router)
app.include_router(chat.router)
app.include_router(conversations.router)
app.include_router(feedback.router)
app.include_router(health.router)


@app.middleware("http")
async def request_logging(request: Request, call_next):
    request_id = request.headers.get("X-Request-Id") or str(uuid.uuid4())
    bind_request_id(request_id)
    start = time.monotonic()
    try:
        response = await call_next(request)
    except Exception:
        logger.exception(
            "request_failed",
            method=request.method,
            path=request.url.path,
            duration_seconds=round(time.monotonic() - start, 3),
        )
        clear_request_context()
        raise

    duration = time.monotonic() - start
    response.headers.setdefault("X-Request-Id", request_id)
    logger.info(
        "request_completed",
        method=request.method,
        path=request.url.path,
        status_code=response.status_code,
        duration_seconds=round(duration, 3),
    )
    clear_request_context()
    return response


@app.exception_handler(AuroraError)
async def aurora_error_handler(request: Request, exc: AuroraError) -> JSONResponse:
    status_code = HTTP_STATUS_BY_CODE[exc.code]
    headers = {}
    if exc.retry_after_seconds is not None:
        headers["Retry-After"] = str(exc.retry_after_seconds)
    return JSONResponse(
        status_code=status_code,
        content={"code": exc.code.value, "message": exc.message},
        headers=headers,
    )


@app.exception_handler(RequestValidationError)
async def validation_error_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    # API_SPEC.md contracts a malformed/invalid body as 400 INVALID_REQUEST, not FastAPI's
    # default 422 — the error code catalogue is the wire contract, so we map to it here.
    return JSONResponse(
        status_code=400,
        content={"code": ErrorCode.INVALID_REQUEST.value, "message": str(exc)},
    )


@app.get("/metrics")
async def metrics() -> Response:
    body, content_type = render_latest()
    return Response(content=body, media_type=content_type)
