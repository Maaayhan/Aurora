# Aurora — Data Model

Three stores, three different jobs. Nothing is duplicated between them without a reason stated here.

## 1. PostgreSQL — durable system of record

Owns anything that must survive a Redis flush and anything needed for analytics/eval later (Phase 1 "evaluation" bonus scope).

```sql
CREATE TABLE users (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  api_key_hash  TEXT UNIQUE,          -- null if this identity only ever uses JWT
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE conversations (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id       UUID NOT NULL REFERENCES users(id),
  title         TEXT,                 -- derived from first message, nullable
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE messages (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  conversation_id UUID NOT NULL REFERENCES conversations(id),
  role            TEXT NOT NULL CHECK (role IN ('user', 'assistant', 'system')),
  content         TEXT NOT NULL,
  model           TEXT,               -- which model/version answered, null for user messages
  latency_ms      INTEGER,            -- null for user messages
  prompt_tokens   INTEGER,
  completion_tokens INTEGER,
  retry_count     INTEGER NOT NULL DEFAULT 0,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_messages_conversation ON messages(conversation_id, created_at);

CREATE TABLE feedback (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  message_id    UUID NOT NULL REFERENCES messages(id),
  rating        SMALLINT NOT NULL CHECK (rating IN (-1, 1)),  -- thumbs down / up
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE model_deployments (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  checkpoint_tag  TEXT NOT NULL,       -- e.g. 'checkpoint-v17'
  status          TEXT NOT NULL CHECK (status IN ('registered','warming','canary','active','rolled_back')),
  traffic_pct     SMALLINT NOT NULL DEFAULT 0,
  promoted_at     TIMESTAMPTZ,
  rolled_back_at  TIMESTAMPTZ
);
```

`messages` is the append-only audit log the JD's "logging, telemetry, and evaluation signals" points at directly — every assistant turn records how long it took, how many tokens, and how many retries it cost, without needing to grep application logs to answer "was this response slow because of retries?"

## 2. Redis — hot path state, nothing that must outlive a restart of its own accord

| Key pattern | Type | TTL | Purpose |
|---|---|---|---|
| `session:{conversationId}` | Hash | sliding, 24h | Cached recent message window so the worker doesn't hit Postgres on every turn |
| `ratelimit:{identityId}` | Sorted set / counter | window length (e.g. 60s) | Rate limiting counters — see `ERROR_HANDLING.md` §Rate limiting for the algorithm |
| `stream:{requestId}` | Pub/Sub channel | n/a (ephemeral) | Worker → gateway token relay (`ARCHITECTURE.md` §5) |
| `cancel:{requestId}` | String flag | 5 min | Set by gateway on client disconnect; polled by worker between chunks |
| `circuit:inference` | Hash (`state`, `failureCount`, `openedAt`) | n/a, explicitly managed | Circuit breaker state, shared across worker replicas |

Redis is explicitly **not** the source of truth for anything — every key here is either a cache of Postgres, a coordination flag, or genuinely ephemeral (a pub/sub message that only matters at the instant it's sent). If Redis is flushed, the system loses in-flight streams and rate-limit windows, not conversation history.

## 3. Celery task payload

Defined once as a Pydantic model in `shared/`, imported by both `app/` (to construct it) and `worker/` (to parse it) — so the two processes can never silently disagree about its shape:

```python
class InferenceTaskPayload(BaseModel):
    request_id: str          # == SSE correlation id == stream:{request_id} channel
    conversation_id: str
    user_id: str
    messages: list[ChatMessage]  # full context window resolved by the API before dispatch
    model: str                # which model/version to route to (bonus: multi-model routing)
    enqueued_at: datetime      # used to compute queue wait time for metrics
```

Resolving the full message context (pulling history from Redis/Postgres) happens in `app/` **before** the task is dispatched, not in `worker/` — this keeps the worker's only external dependency the model provider, and keeps "what did we actually send the model" fully determined by the task payload, which makes replaying or auditing a specific request possible from the task data alone. SQLAlchemy 2.0 (async) is the ORM for the Postgres tables in §1, with Alembic managing schema migrations (`migrations/`).

## 4. Why not just one store

A tempting simplification is "just use Postgres for everything, skip Redis." Rejected because rate-limit counters and pub/sub streaming are fundamentally high-frequency, low-durability-requirement operations — routing them through Postgres would mean every token chunk becomes a database write, and rate-limit checks (which must happen on every request, including rejected ones) would add relational-database latency to the hot path. Redis is the right tool because none of its jobs here need ACID guarantees; they need speed and TTL-based cleanup.
