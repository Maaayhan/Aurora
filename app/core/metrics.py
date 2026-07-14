"""The 7 Prometheus metrics required by ROADMAP.md Phase 1.

TTFT (time to first token) and TPOT (time per output token) are what make
this look like real inference-serving observability rather than generic API
metrics with a chat endpoint bolted on (ROADMAP.md Phase 1) — everything else
here is a fairly standard web-service metric set.
"""

from prometheus_client import (
    CONTENT_TYPE_LATEST,
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)

registry = CollectorRegistry()

requests_total = Counter(
    "aurora_requests_total",
    "Total requests to POST /chat, by terminal status",
    ["status"],
    registry=registry,
)

request_duration_seconds = Histogram(
    "aurora_request_duration_seconds",
    "End-to-end POST /chat request duration",
    registry=registry,
)

rate_limit_rejections_total = Counter(
    "aurora_rate_limit_rejections_total",
    "Requests rejected by the sliding-window rate limiter",
    registry=registry,
)

inference_errors_total = Counter(
    "aurora_inference_errors_total",
    "Inference failures forwarded to a client as an SSE error event, by error code",
    ["code"],
    registry=registry,
)

queue_depth = Gauge(
    "aurora_queue_depth",
    "Approximate depth of the default Celery queue at last observation",
    registry=registry,
)

# 【卷 四.六.二】这两个指标是整个监控体系里最"懂推理服务"的部分，普通 Web API 不会有这两个概念：
# 【卷】TTFT（首字延迟）——从用户发出请求，到第一个 token 吐出来要多久，直接决定用户"感觉快不快"；
# 【卷】TPOT（每 token 耗时）——生成过程中平均吐一个 token 要多久，决定"读起来顺不顺"。
# 【卷】这两个指标只有在真正理解"流式推理"这个场景（而不是把它当成普通的一次性 HTTP 请求）
# 【卷】才会想到要measure。具体在哪里被记录、怎么算出来的，去看 七.一 app/api/chat.py 的
# 【卷】_sse_generator——第一次收到 token 事件时记 TTFT，收到 done 事件时用 completion_tokens
# 【卷】反推 TPOT。
ttft_seconds = Histogram(
    "aurora_ttft_seconds",
    "Time to first token — request accepted to first SSE token event",
    registry=registry,
)

tpot_seconds = Histogram(
    "aurora_tpot_seconds",
    "Time per output token, averaged over a completed generation",
    registry=registry,
)


def render_latest() -> tuple[bytes, str]:
    return generate_latest(registry), CONTENT_TYPE_LATEST
