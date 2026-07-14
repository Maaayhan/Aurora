# Aurora — Architecture

## 1. Layers

```
Client
  │  HTTPS, SSE
  ▼
API Layer            (app/ — FastAPI)
  │  auth, rate limit, validation, session lookup, enqueue task, relay stream
  ▼
Application Layer     (app/services — session, conversation, queue orchestration)
  │  Celery task + Redis pub/sub — no direct network hop to the model from here
  ▼
Inference Layer       (worker/ — Celery worker)
  │  provider interface, timeout/retry/circuit-breaker, streaming token relay
  ▼
Model                 (Ollama locally / vLLM in production, serving Aurora-13B's stand-in)
```

The rule that makes this a platform and not a wrapper script: **the application layer never talks to the model.** It talks to a queue. Only the inference layer (a separate process) holds a client to the model provider. This is what makes "the model can only usefully handle N concurrent requests" an enforceable property of the system rather than a comment nobody reads.

## 2. Why a queue at all, and why Celery

If `app/` called the model provider directly inside a request handler, two things become impossible to do correctly:

- **Bounding concurrency at the model.** A single local Ollama instance (standing in for a GPU-bound inference engine) can only usefully serve a handful of concurrent generations before latency degrades for everyone. Without a queue, "N requests processing, the next one waits" has nowhere to live — it would have to be reinvented as an in-process semaphore that doesn't survive a process restart and doesn't work once there's more than one API instance.
- **Decoupling request acceptance from request execution.** The API should be able to say "I've accepted your request" immediately and stay responsive to new connections, health checks, and cancellations while inference runs elsewhere. A queue is what makes "accepted" and "executing" different moments in time.

Celery (broker = Redis, same Redis already used for sessions and rate limiting) is chosen over `arq` or a hand-rolled asyncio queue because it's the mature, batteries-included choice in the Python ecosystem: built-in retry with backoff, a well-understood worker concurrency model, and Flower gives a queue/worker dashboard for free (`ROADMAP.md` Phase 3). The tradeoff, made explicit rather than discovered later, is that Celery's defaults are tuned for "fire-and-forget background jobs," not "long-running task whose partial output needs to stream out live" — two specific things have to be configured deliberately instead of left at default, both covered in `ERROR_HANDLING.md`:

- `task_acks_late=True` + `task_reject_on_worker_lost=True` — without this, Celery acknowledges (and discards) a task the moment a worker *picks it up*, not when it finishes. A worker that dies mid-inference would silently lose the job instead of requeuing it.
- Cancellation is handled cooperatively (a Redis flag polled between token chunks), not via `task.revoke(terminate=True)` — in Celery's default prefork pool, `terminate=True` sends `SIGTERM` to the whole worker process, which is a much blunter instrument than "stop this one generation."

## 3. Why the worker is a separate process from the API

This is the one deliberate structural decision beyond the literal request/response cycle, so it's worth defending explicitly.

A queue that is *produced to and consumed from inside the same process* is not really a queue — it's a to-do list with extra steps. The properties that make a queue worth having in a production serving system only show up once producer and consumer can scale, fail, and restart independently:

- **`app/`** (FastAPI, run under Uvicorn) scales horizontally purely based on HTTP concurrency (connections, auth checks, SSE fan-out) — it does zero model-bound work, so it never needs to know how many model replicas exist.
- **`worker/`** (Celery) scales based on inference capacity — one worker process (or `--concurrency=N`) per available model replica, expressing directly "this worker will run at most N provider calls at once."
- A worker crash (e.g., the model provider becomes unresponsive and the process is killed by a supervisor) does not take the API down — in-flight SSE connections degrade gracefully (`ERROR_HANDLING.md`) instead of the whole service going 502.
- It mirrors how this would actually be built at a company running real GPU infrastructure: the process holding a client to an inference engine is never the same process terminating public internet connections.

The cost of this split is that streaming has to cross a process boundary — solved with Redis pub/sub, detailed in §5.

## 4. The model provider boundary

`ModelProvider` (a `typing.Protocol` in `providers/base.py`) is the only thing the worker is allowed to depend on for talking to a model:

```python
class ModelProvider(Protocol):
    async def chat(
        self, messages: list[ChatMessage], *, cancel_check: Callable[[], bool],
    ) -> AsyncIterator[ChatChunk]: ...

    async def health_check(self) -> ProviderHealth: ...
```

- **`OllamaProvider`** — local development, calls Ollama's OpenAI-compatible `/v1/chat/completions` (`stream: true`) over HTTP. Runs natively on the Mac mini host (not in Docker — see `DEPLOYMENT.md` §1 for why), so the container calls it via `host.docker.internal:11434`.
- **`VLLMProvider`** — the future-production implementation of the same interface, targeting an NVIDIA GPU host running vLLM's OpenAI-compatible server. Not built in this project (no GPU cluster available), but the interface is designed to make swapping to it a config change (`MODEL_PROVIDER=ollama|vllm`), not a rewrite.
- **`MockProvider`** — deterministic canned responses for tests, so `worker/` tests don't depend on a running model at all.

Nothing above this interface — queue logic, session logic, the API layer — knows or cares that the backing runtime is Ollama specifically. This is the direct implementation of the PRD's tradeoff on Ollama vs. vLLM, and it's why the local/Mac constraint doesn't leak into application code anywhere.

## 5. Bridging streaming across the API/worker split

This is the trickiest sequence in the system and the one most worth being able to explain precisely (see `SEQUENCE_DIAGRAMS.md` §1 for the diagram):

1. Client `POST /chat` → API. API authenticates, rate-limit-checks, persists the user message, generates a `request_id`, dispatches a Celery task `run_inference.delay(request_id, conversation_id, messages)`, and immediately opens an SSE response — it does **not** wait for the task to be picked up.
2. API subscribes to Redis pub/sub channel `stream:{request_id}` before yielding control to the SSE generator, so no chunk can be published before the subscriber exists.
3. Worker picks up the task (respecting its configured `--concurrency`), calls `provider.chat(...)`, and for each token chunk received, `PUBLISH`es it to `stream:{request_id}`.
4. API's subscription loop forwards each published message to the client as an SSE `data:` event, in the order received.
5. On stream completion (or error, or cancellation — see `ERROR_HANDLING.md`), the worker publishes a terminal event (`{"type": "done", ...}` or `{"type": "error", "code": ...}`) on the same channel; the API forwards it, closes the SSE response, and unsubscribes.

Any API process instance can serve the client, and any worker can execute the task, because Redis pub/sub is the only coupling between them — neither side needs the other's process identity.

## 6. Module responsibilities

**`app/`** (FastAPI)

| Module | Responsibility | Explicitly not responsible for |
|---|---|---|
| `app/core/auth.py` | JWT + API key dependency, identity resolution | Rate limit decisions |
| `app/core/rate_limit.py` | Redis-backed sliding window, 429 responses | Queue depth or inference concurrency |
| `app/api/chat.py` | `POST /chat`, SSE response, request validation (Pydantic) | Talking to the model directly |
| `app/services/session_service.py` | Conversation CRUD, history retrieval (Postgres + Redis-cached) | Deciding when a conversation is "done" |
| `app/services/queue_service.py` | Dispatches the Celery task, owns the pub/sub subscribe/forward bridge | Executing inference |
| `app/api/health.py` | Aggregates health of Postgres, Redis, Celery, and (via a lightweight ping) the model provider | — |

**`worker/`** (Celery)

| Module | Responsibility | Explicitly not responsible for |
|---|---|---|
| `worker/tasks.py` | Celery task entrypoint, owns the `ModelProvider` instance, publishes stream chunks | HTTP concerns, auth, rate limiting |
| `worker/reliability.py` | Retry-with-backoff, circuit breaker state, timeout enforcement, cooperative cancellation polling | Deciding *whether* to accept a request (that's the API's job, before dispatch) |

**`providers/`**: `ModelProvider` protocol + `OllamaProvider` / `VLLMProvider` / `MockProvider`, imported by `worker/` only.

**`shared/`**: Pydantic models used by both `app/` and `worker/` — the task payload shape, SSE event shape, and the error code enum — so a message means the same thing on both sides of the process boundary.

## 7. Why this split keeps route handlers thin

The spec's coding requirement ("no giant handlers, single-responsibility services") falls out of the layering above: a FastAPI route in `app/api/chat.py` does exactly three things — validate the request via a Pydantic model (declared in the function signature, no manual parsing), call `ChatService.send_message()`, and return a `StreamingResponse` wired to the SSE generator. `ChatService` orchestrates (check rate limit → persist message → dispatch task → subscribe); it does not itself implement rate limiting, persistence, or queueing — those are separate services with one job each. No layer reaches past the one directly below it (`app/` never imports a provider class; `worker/` never imports FastAPI).

## 8. What's intentionally not decided yet

- Exact rate limit numbers (RPM, burst allowance) — placeholder in `API_SPEC.md`, tunable via env, not a design question worth freezing this early.
- Whether Postgres stores full message content or the source of truth stays Redis with Postgres as durable overflow — decided in `DATA_MODEL.md` once conversation retention requirements are pinned down in Phase 1.
- Multi-model routing (bonus scope) — deferred to `ROADMAP.md` Phase 4+; the provider boundary in §4 is what makes it possible later without rework.
- Which local model size the Mac mini can comfortably run (`Qwen2.5:3b`/`llama3.2:3b` as the safe default, larger sizes as a config change if unified memory allows) — a hardware-fit question, not an architecture question, resolved empirically once Ollama is actually running locally.
