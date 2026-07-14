# Aurora — Sequence Diagrams

## 1. Happy path: chat request with streaming

```mermaid
sequenceDiagram
    participant C as Client
    participant A as API (app/, FastAPI)
    participant R as Redis
    participant Q as Celery Queue (Redis broker)
    participant W as Worker (worker/, Celery)
    participant O as Model Provider (Ollama/vLLM)

    C->>A: POST /chat (SSE)
    A->>A: authenticate, check rate limit
    A->>R: persist user message context
    A->>Q: dispatch run_inference.delay(request_id)
    A->>R: SUBSCRIBE stream:{request_id}
    A-->>C: 200, SSE stream open

    W->>Q: pick up task (respects --concurrency limit)
    W->>O: POST /v1/chat/completions (stream:true)
    loop each token
        O-->>W: token chunk
        W->>R: PUBLISH stream:{request_id}
        R-->>A: message delivered (subscriber)
        A-->>C: event: token
    end
    O-->>W: stream complete
    W->>R: PUBLISH stream:{request_id} {type: done}
    A-->>C: event: done
    A->>R: UNSUBSCRIBE
    W->>A: (async) task result persisted to Postgres
```

## 2. Client disconnect / cancellation

```mermaid
sequenceDiagram
    participant C as Client
    participant A as API
    participant R as Redis
    participant W as Worker
    participant O as Model Provider

    C--)A: TCP connection closed (browser tab closed)
    A->>A: request disconnect callback fires
    A->>R: SET cancel:{request_id} = 1 (TTL 5m)
    A->>R: UNSUBSCRIBE stream:{request_id}

    Note over W: worker is mid-stream, checks cancel flag between chunks
    W->>R: GET cancel:{request_id}
    R-->>W: "1"
    W->>O: cancel in-flight httpx request (asyncio task cancellation)
    W->>W: mark task cancelled, do not retry
    Note over W: no PUBLISH — no one is listening anymore
```

Cancellation is checked *between* chunks, not via a blocking call — the worker's async read loop already yields control per-token, so this is a cheap poll, not a new concurrency primitive. It deliberately does **not** use Celery's `task.revoke(terminate=True)` — in the default prefork pool that sends `SIGTERM` to the entire worker process, killing every other task it might be running, not just this one.

## 3. Inference failure: retry vs. fail-loud

```mermaid
sequenceDiagram
    participant W as Worker
    participant CB as Circuit Breaker (in-process + circuit:inference in Redis)
    participant O as Model Provider
    participant A as API
    participant C as Client

    W->>CB: may I call inference?
    CB-->>W: yes (closed)
    W->>O: POST /v1/chat/completions
    O--xW: connection refused / timeout, ZERO tokens streamed yet
    W->>CB: report failure
    CB->>CB: failure_count++

    alt failure_count < threshold, retries remain
        W->>W: backoff (0.5s, 1s, 2s) via Celery self.retry(countdown=...)
        W->>O: retry
        O-->>W: success, tokens stream normally
    else retries exhausted OR failure occurs after tokens already streamed
        W->>A: PUBLISH stream:{request_id} {type: error, code: INFERENCE_TIMEOUT}
        A-->>C: event: error
        Note over W,C: never silently retries after partial output —<br/>a half-sent answer can't be un-sent to the client
    end

    alt failure_count >= threshold within window
        CB->>CB: state = OPEN, opened_at = now
        Note over CB: subsequent calls fail fast with INFERENCE_UNAVAILABLE<br/>for cooldown period, without touching the model provider at all
    end
```

The load-bearing rule here, worth stating out loud in an interview: **retries are only safe before the first byte of output has reached the client.** Once tokens are streaming, a failure becomes a terminal `error` event, never a silent retry — because the client has already rendered partial output it can't be told to un-render.

## 4. Backpressure: queue is full

```mermaid
sequenceDiagram
    participant C as Client
    participant A as API
    participant Q as Celery Queue (Redis broker)

    C->>A: POST /chat
    A->>Q: check queue depth
    Q-->>A: depth = 500 (over threshold)
    A-->>C: 503 SERVER_BUSY, Retry-After: 5
    Note over A,Q: request is rejected before dispatch —<br/>the queue itself never grows unbounded
```

## 5. Checkpoint deployment

```mermaid
sequenceDiagram
    participant R as Research team
    participant Reg as Model Registry
    participant D as Deploy pipeline
    participant W as Worker fleet
    participant H as Health check
    participant T as Traffic router

    R->>Reg: register checkpoint-v17
    D->>W: pull v17, start warm instance (0% traffic)
    D->>H: run smoke tests against warm instance
    alt smoke tests pass
        D->>T: shift 10% traffic to v17 (canary)
        D->>D: monitor error rate / latency for canary window
        alt canary healthy
            D->>T: shift 100% traffic to v17
            D->>Reg: mark v17 active, v16 retired
        else canary unhealthy
            D->>T: shift traffic back to v16
            D->>Reg: mark v17 rolled_back
        end
    else smoke tests fail
        D->>Reg: mark v17 rolled_back, never receives traffic
    end
```

Detailed in `DEPLOYMENT.md`; this diagram is the one-page version.
