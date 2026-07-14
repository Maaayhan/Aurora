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
