# Aurora — API Specification

Full interactive contract lives at FastAPI's auto-generated `/docs` (Swagger UI) and `/openapi.json` once `app/` exists — generated directly from the Pydantic models in `app/schemas/`, not hand-written separately. This document is the source of truth those schemas are written *from* — decisions made here, not reverse-engineered from code later.

## Auth

Two accepted credential types on every request (except `/health`):

- `Authorization: Bearer <jwt>` — HS256, short-lived (15 min access token), issued by a `POST /auth/token` exchange (not detailed further — out of scope beyond "exists and is standard").
- `X-API-Key: <key>` — for service-to-service callers who don't want a token refresh cycle. Stored server-side as a hash (`users.api_key_hash`), never compared in plaintext.

Both resolve to the same internal `identityId` used everywhere downstream (rate limiting, message attribution).

## `POST /chat`

Starts (or continues, if `conversationId` is given) a conversation and streams the assistant's reply.

**Request**
```json
{
  "conversationId": "uuid | null",
  "message": "string, required, max 4000 chars",
  "model": "string, optional, defaults to configured default model"
}
```

**Response**: `200`, `Content-Type: text/event-stream`. Each SSE event:

```
event: token
data: {"content": "Hello"}

event: token
data: {"content": ", world"}

event: done
data: {"requestId": "...", "promptTokens": 12, "completionTokens": 34, "latencyMs": 812}
```

or, on failure mid-stream:

```
event: error
data: {"code": "INFERENCE_TIMEOUT", "message": "The model did not respond in time.", "retryable": true}
```

The stream always ends in exactly one of `event: done` or `event: error` — a client can rely on "connection closed without one of these" meaning a transport-level failure, distinct from an application-level one.

**Sync errors** (returned before the stream opens, as normal JSON + status code):

| Status | Code | When |
|---|---|---|
| 400 | `INVALID_REQUEST` | Message too long, missing, or malformed body |
| 401 | `UNAUTHENTICATED` | No/invalid credential |
| 404 | `CONVERSATION_NOT_FOUND` | `conversationId` given but doesn't belong to this identity |
| 429 | `RATE_LIMITED` | Over the request quota — `Retry-After` header set |
| 503 | `SERVER_BUSY` | Queue depth over threshold — see `ERROR_HANDLING.md` §Backpressure |

## `GET /conversations/:id`

Returns conversation metadata + message history (paginated, `?cursor=&limit=`). 404 if not owned by the caller's identity.

## `GET /conversations`

Lists the caller's conversations, most recently updated first.

## `POST /messages/:id/feedback`

```json
{ "rating": 1 }   // or -1
```
Writes to `feedback`. 404 if the message doesn't exist or isn't the caller's.

## `GET /health`

No auth required (used by orchestrators/load balancers).

```json
{
  "status": "healthy | degraded | unhealthy",
  "checks": {
    "postgres": { "healthy": true, "latencyMs": 3 },
    "redis":    { "healthy": true, "latencyMs": 1 },
    "queue":    { "healthy": true, "depth": 2 },
    "inference": { "healthy": true, "latencyMs": 45 }
  }
}
```
`degraded` = inference layer unhealthy but the API itself can still accept/queue requests. `unhealthy` = Postgres or Redis unreachable (the gateway cannot function at all). This distinction is what a load balancer or orchestrator needs to decide "stop routing here" vs. "this instance just can't serve inference right now, which might be true everywhere."

## `GET /metrics`

Prometheus exposition format. Metrics list in `docs/ROADMAP.md` Phase 1 / Phase 3.

## Error code catalogue

A single enum shared between `app/` and `worker/` (`shared/errors.py`), so an error code means the same thing everywhere it appears — in an SSE `error` event, an HTTP JSON error body, or a log line.

| Code | Meaning |
|---|---|
| `INVALID_REQUEST` | Client sent a malformed request |
| `UNAUTHENTICATED` | No valid credential |
| `CONVERSATION_NOT_FOUND` | Requested resource doesn't exist / isn't owned by caller |
| `RATE_LIMITED` | Quota exceeded |
| `SERVER_BUSY` | Queue backpressure — request rejected before enqueue |
| `INFERENCE_TIMEOUT` | Worker gave up waiting on the model past the configured deadline |
| `INFERENCE_UNAVAILABLE` | Circuit breaker open — model layer is known-bad right now |
| `INFERENCE_ERROR` | Model call failed for a reason other than timeout (e.g. malformed response) |
| `CANCELLED` | Client disconnected; request was stopped intentionally, not a failure |
