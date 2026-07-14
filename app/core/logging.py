"""Structured JSON logging, shared by app/ and worker/ — DEPLOYMENT.md §4.

Every log line carries `request_id` when one exists (bound via `bind_request_id`
into a contextvar-backed structlog context), so a single request's path across
API -> queue -> worker -> model provider can be reconstructed by filtering on
one field, without a tracing backend (Phase 3 adds that; this doesn't get
thrown away once it exists — DEPLOYMENT.md §4).
"""

import logging
import sys

import structlog


def configure_logging(log_level: str = "INFO") -> None:
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=getattr(logging, log_level.upper(), logging.INFO),
    )

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.stdlib.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, log_level.upper(), logging.INFO)
        ),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    return structlog.get_logger(name)


# 【卷 四.六.一】这两个函数是"跨进程日志关联"的关键机制。原理：structlog 用 Python 的
# 【卷】contextvars（跟 asyncio 的执行上下文绑定的变量）存 request_id，bind 一次之后，
# 【卷】这个请求接下来（在同一个进程、同一条执行路径里）打的每一条日志都会自动带上这个字段，
# 【卷】不用每次 logger.info() 都手动传 request_id 参数。app/ 在收到请求时 bind 一次（七.二
# 【卷】main.py 的中间件），worker/ 在收到任务时也 bind 一次（六.三.二 tasks.py）——因为
# 【卷】request_id 是通过 InferenceTaskPayload（二.二）从 app/ 传到 worker/ 的同一个值，
# 【卷】所以两个进程各自打的日志，虽然物理上完全独立，但过滤同一个 request_id 就能拼出
# 【卷】"这个请求在 API 和 worker 里分别做了什么"的完整时间线——这就是 DEPLOYMENT.md 说的
# 【卷】"没有链路追踪系统之前，先靠这个手段兜底"。
def bind_request_id(request_id: str) -> None:
    structlog.contextvars.bind_contextvars(request_id=request_id)


def clear_request_context() -> None:
    structlog.contextvars.clear_contextvars()
