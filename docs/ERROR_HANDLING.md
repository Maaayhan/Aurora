# Aurora — Error Handling & Failure Modes

Every row here is a scenario the system is designed to survive without an operator intervening. "Survive" means: return a clear, typed error to the caller, log enough to diagnose it, and leave the system in a state where the next request has a normal chance of succeeding.

## 1. Rate limiting

**Algorithm**: sliding window log, per `identityId`, stored in Redis as a sorted set (`ratelimit:{identityId}`, score = request timestamp). On each request: `ZREMRANGEBYSCORE` to drop entries older than the window, `ZCARD` to count what's left, reject if `>= limit`, else `ZADD` the current timestamp.

Chosen over fixed-window counters because fixed windows allow a burst of `2x limit` right at a window boundary (limit at 23:59:59, limit again at 00:00:00) — a sliding log doesn't have that edge. Chosen over a token bucket because the requirement here is "N requests per window," not "smoothed throughput with burst allowance" — token bucket is the right tool when burst tolerance matters; it doesn't here.

**On limit exceeded**: `429`, code `RATE_LIMITED`, `Retry-After` header set to the number of seconds until the oldest entry in the window expires. The request is rejected **before** touching the queue — a client that's over quota should never be able to add load to the inference layer at all.

## 2. Timeouts

Two independent timeouts, easy to conflate and worth keeping distinct:

- **Queue wait timeout** (default 60s): if a task sits in the Celery queue longer than this before a worker picks it up, it's failed with `SERVER_BUSY` rather than eventually running — a response that arrives after the client gave up waiting has negative value; better to fail fast and let the client retry when there's actually capacity.
- **Inference timeout** (default 30s, per attempt): once a worker starts a call to the model provider, an `asyncio.wait_for` deadline fires if no data arrives in time, cancelling the underlying `httpx` request. This is a *per-attempt* timeout, so it applies independently to each retry, not once across the whole task.

## 3. Retry policy

- Retries apply **only** to failures that occur before any token has been streamed to the client (see `SEQUENCE_DIAGRAMS.md` §3 for why). Once streaming has started, any subsequent failure is terminal for that request — reported as an `error` SSE event, never retried.
- Exponential backoff: `0.5s → 1s → 2s`, max 3 attempts total (1 original + 2 retries), configurable via env.
- Retries use Celery's native `autoretry_for` / `self.retry(countdown=...)` mechanism, not a hand-rolled loop — combined with `task_acks_late=True` (see §6), this means retry state survives a worker crash between attempts (the task just gets picked up by another worker, with Celery's own retry counter intact).
- A retry is logged with the same `request_id` and an incrementing `retry_count`, so `messages.retry_count` in Postgres (see `DATA_MODEL.md`) reflects the true cost of a response, not just its final latency.

## 4. Circuit breaker

Per-worker in-memory state, mirrored to `circuit:inference` in Redis so all worker replicas converge on the same view without every one of them needing to independently rediscover that the model is down.

- **Closed** (normal): requests pass through freely, failures increment a counter with a sliding time window.
- **Open**: triggered when failures exceed a threshold (default: 5 failures in 30s) — for the cooldown period (default 20s), calls to Ollama are skipped entirely; the worker fails the job immediately with `INFERENCE_UNAVAILABLE`. This is the mechanism that stops a struggling model from being hit with a wall of retrying requests while it's trying to recover.
- **Half-open**: after cooldown, the next single request is allowed through as a probe. Success → closed (counter reset). Failure → open again, cooldown restarts.

This is the direct implementation of the spec's "model fails repeatedly → backend stops sending, prevents cascading failure" requirement.

## 5. Backpressure

Queue depth is checked synchronously at request time, before enqueue (`SEQUENCE_DIAGRAMS.md` §4). Threshold is a configured max queue length (default 200, tunable based on observed worker throughput). Over threshold → `503 SERVER_BUSY` immediately, no enqueue attempt. This is what keeps "queue got long" from becoming "queue grew unbounded and the Redis instance backing it OOMs" — the failure is pushed back to the edge (reject the request) instead of absorbed internally where it would eventually surface as a much worse failure.

## 6. Worker crash mid-job

Celery's default behavior acknowledges (and discards) a task the moment a worker *receives* it, not when it finishes — a worker that dies mid-inference would silently lose that task with no retry. This is disabled explicitly: `task_acks_late=True` + `task_reject_on_worker_lost=True` in the Celery config, so a task is only acked on success/explicit failure, and a task whose worker disappears mid-execution is put back on the queue for another worker to pick up. This is deliberately called out because it is *not* the default — a Celery deployment that never sets these two flags looks identical to one that does, right up until a worker crashes in production and jobs vanish without a trace. If the crash happened after tokens had already started streaming to a client, that client's SSE connection also dies (the publishing worker is gone) — the client sees a closed connection with no `done`/`error` event, which per `API_SPEC.md` is explicitly documented as "treat as transport failure, safe to retry the whole request." (The requeued task itself is still subject to the "no retry after partial output" rule in §3 — from the *new* worker's perspective this is a fresh attempt with no tokens sent yet, so retrying it is safe; it's only unsafe from the perspective of a worker retrying its own already-streaming attempt.)

## 7. Dependency outages

| Dependency down | Effect | `/health` reports |
|---|---|---|
| Postgres unreachable | Gateway cannot authenticate new sessions or persist messages — **hard failure**, `5xx` on all non-health endpoints | `status: unhealthy` |
| Redis unreachable | Rate limiting, queueing, and streaming all break — **hard failure** (Redis is load-bearing, not optional caching, per `DATA_MODEL.md` §4) | `status: unhealthy` |
| Ollama unreachable | Existing conversations/history still readable; new inference requests fail fast via the circuit breaker once it trips | `status: degraded` |

The Postgres/Redis-down cases are deliberately **not** treated as "degrade gracefully" — there is no safe partial-functionality mode when the system can't authenticate or track state, and pretending otherwise (e.g., silently serving stale cached auth) would be a worse failure than an honest 503.

## 8. Malformed or hostile input

- Request body validated via Pydantic models at the route boundary (FastAPI rejects anything that doesn't match the schema before the handler body even runs) — rejected with `400 INVALID_REQUEST`, so no partially-validated request ever reaches the queue.
- Message length capped (4000 chars) to bound both queue payload size and the prompt sent to the model.
- The model provider's response is trusted as opaque token text and streamed through verbatim (no `eval`, no template interpolation of model output anywhere) — the one place this would matter (if Aurora ever rendered model output as HTML) is explicitly out of scope since there's no UI, but the principle is documented here so it isn't forgotten if a client is ever built.

## 9. What "graceful failure" explicitly excludes

Worth stating because it's easy to over-scope "graceful": Aurora does not attempt to silently substitute a different model, fabricate a plausible-looking response when inference fails, or retry indefinitely hoping for success. Every failure mode above resolves to either "succeed" or "a typed error the caller can act on" — never a response that looks successful but isn't backed by an actual model completion.
