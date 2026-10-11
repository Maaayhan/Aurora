# Aurora — Development Log

Audit trail of work completed and the reasoning behind non-obvious decisions, in chronological order (newest last). This is not a changelog for users — it's a record for later review of *why* something was built the way it was, so a design choice never has to be reverse-engineered from a diff.

Each entry: date, what changed, why (only when the reason isn't already obvious from `docs/`), and any deviation from the design docs (should be rare — deviations should update the relevant doc too).

---

## 2026-07-14

**Started Phase 1 implementation.** Phase 0 design docs (`PRD.md`, `ARCHITECTURE.md`, `DATA_MODEL.md`, `API_SPEC.md`, `ERROR_HANDLING.md`, `DEPLOYMENT.md`, `SEQUENCE_DIAGRAMS.md`, `ROADMAP.md`) were read in full before writing any code, per the Phase 0 exit criterion in `ROADMAP.md` ("done when... can be explained out loud from a cold start").

Set up two tracking artifacts requested for the build phase:
- `docs/KANBAN.md` — card-per-deliverable board, seeded from the Phase 1–4 scope tables in `ROADMAP.md` so the board and the roadmap can't silently diverge.
- `docs/DEVLOG.md` (this file) — audit log of completed work and decision rationale.

Both are living documents, updated as part of finishing each unit of work, not retroactively.

**Environment.** This machine had no Python 3.12, no `uv`/`poetry`, no Docker, no Ollama. Asked the
user how much environment setup to do autonomously (full setup incl. Docker/Ollama vs. lightweight
vs. code-only) — got no response within the turn, so proceeded with the lightweight path: installed
`uv` (via Homebrew) and Python 3.12.13 (via `uv python install`), enough to write and actually run
code with unit/integration tests. Deliberately did **not** install Docker Desktop or Ollama
autonomously — those are heavier, GUI/bandwidth-involved changes (Ollama pulls multi-GB model
weights) better confirmed first. This is the one open blocker on Phase 1's real definition of done:
code is written and tested against `MockProvider`/SQLite/fakeredis, but not yet against a live
Postgres/Redis/Ollama stack.

**Built, in dependency order:** project scaffolding (`pyproject.toml`, `.env.example`,
`docker-compose.yml`, `docker/Dockerfile.{api,worker}`) → `shared/` (error codes, Pydantic
models shared across the process boundary) → `providers/` (`ModelProvider` Protocol,
`OllamaProvider`, `MockProvider`, `factory.py`) → Postgres layer (SQLAlchemy 2.0 async models +
hand-written Alembic migration) → `app/core/` (config, auth, rate limiting, Redis, logging,
metrics) → `app/services/` (`session_service`, `queue_service`) → `worker/` (`celery_app.py`,
`tasks.py`, `reliability.py`) → `app/api/` (`chat.py`, `auth.py`, `conversations.py`,
`feedback.py`, `health.py`) → `app/main.py` wiring it all together → the Phase 1 test suite (39
tests: unit tests for isolated modules, integration tests against the real FastAPI app with
Postgres→SQLite, Redis→fakeredis, and a `dispatch_inference` test double standing in for a live
Celery worker).

**Non-obvious decisions, and why:**

- **API keys are hashed with SHA-256, not bcrypt** (`app/core/auth.py:hash_api_key`). `DATA_MODEL.md`
  only specifies `api_key_hash TEXT UNIQUE`, not an algorithm. Bcrypt is the right tool for
  low-entropy secrets (passwords) where slow hashing resists brute force, but it can't be used as a
  unique-indexed lookup key (verifying means comparing against every row). API keys are
  high-entropy random tokens already, so a fast deterministic hash that supports an O(1) indexed
  lookup is the correct tradeoff — the same approach Stripe/GitHub-style API key auth uses.
- **`ModelProvider.chat` is typed `def`, not `async def`, in the Protocol** (`providers/base.py`),
  even though `ARCHITECTURE.md §4`'s illustrative pseudocode writes it as `async def`. Real
  implementations are async *generator* functions (`async def chat(...): yield ...`) — calling one
  returns an `AsyncIterator` synchronously, it is never itself awaited. Typing it `async def` tells
  a type checker to expect `await provider.chat(...)`, which is wrong for every concrete
  implementation and was caught by `mypy`, not by inspection.
- **The worker persists the assistant's reply to Postgres, not the API.**
  `SEQUENCE_DIAGRAMS.md §1` shows this as an async step after the terminal SSE event, and it makes
  sense structurally: the worker is the one that knows `retry_count` (via Celery's own retry
  counter) and has the full accumulated content already in memory from streaming it. One
  consequence, worth flagging because it surprised a test: the client can receive `event: done`
  *before* the assistant message is durably committed (`worker/tasks.py:run_inference` publishes
  `done` and only then calls `_persist_assistant_message`) — a client that immediately re-fetches
  conversation history right after `done` could theoretically race the write. `SEQUENCE_DIAGRAMS.md`
  already flags this step `(async)`, so this is being treated as a documented, accepted tradeoff for
  Phase 1, not a bug — worth a real look in Phase 2/3 if it ever shows up as a flaky read in
  practice.
- **`worker/tasks.py`'s Celery task dispatch goes through `worker/celery_app.py` (config only), never
  `worker/tasks.py` directly.** `app/services/queue_service.py` imports the former and dispatches by
  task *name* (`celery_app.send_task("worker.tasks.run_inference", ...)`) specifically so the API
  process's import graph never reaches `providers/` — keeping `ARCHITECTURE.md §6`'s "`app/` never
  imports a provider class" rule true even though app/ and worker/ share the same Celery app object.
- **The initial Alembic migration was hand-written, not autogenerated** — there was no live Postgres
  instance in this environment to diff against. It's a direct transcription of `DATA_MODEL.md §1`;
  flagged in the migration's own docstring so nobody mistakes it for machine-verified.
- **`GET /health`'s queue depth and the `aurora_queue_depth` metric are an approximation**
  (`LLEN` on the Celery broker's default queue key), not a Celery `inspect()` call — cheaper, doesn't
  require a live worker to answer, and matches what `SEQUENCE_DIAGRAMS.md §4` implies backpressure
  checking looks like ("check queue depth" as a fast synchronous read).

**Bugs the test suite caught before they'd have been embarrassing:**

1. `session_service.append_message` set `conversations.updated_at = message.created_at`, but
   `message.created_at` is `None` in Python until the row is actually flushed (it's a
   `server_default=func.now()` column) — every second message in a conversation would have thrown
   `IntegrityError: NOT NULL constraint failed`. Fixed by using `func.now()` directly in the update
   statement instead of relying on the ORM object's unrefreshed attribute.
2. `worker/tasks.py:_run_inference_async` misreported a request cancelled *before any token was
   yielded* as `status="success"` — the cancellation check lived inside the `async for` loop body,
   but a provider whose generator honors `cancel_check` internally (as both `MockProvider` and
   `OllamaProvider` do) can return with zero iterations, so that check never ran. Fixed by checking
   `cancel_check()` once more after the loop exits, before declaring success. Caught by
   `tests/integration/test_worker_inference.py::test_cancellation_stops_the_stream_without_publishing_a_terminal_event`.

**Verification so far:** `uv run pytest` — 39 passed. `uv run ruff check .` — clean. `uv run mypy app
worker providers shared` — clean (36 source files). `GET /health` manually confirmed to fail fast
(143ms) and report `unhealthy` with per-dependency diagnostics when Postgres/Redis/Ollama aren't
running, rather than hanging — the exact behavior `ERROR_HANDLING.md §7` specifies.

**Not yet done:** real Postgres/Redis/Ollama end-to-end run (blocked on the environment-setup
question above), Phase 2 (circuit breaker, backpressure enforcement, cancellation demoed live),
Phase 3 (tracing/dashboards), Phase 4 (deploy/rollback scripts).

---

**Later the same day: installed Docker + Ollama and ran Phase 1 end-to-end for real.** User
confirmed the earlier install question. This surfaced a second, more urgent constraint: local disk
had only ~2.7GB free, not enough for Docker's VM image plus Postgres/Redis/api/worker images.

**Storage relocated to an external drive (`/Volumes/Samsung870EVO`, 155GB free) before any of it
could fill the local disk:**
- Ollama models (`OLLAMA_MODELS` env var, set via `EnvironmentVariables` in the brew-services
  launchd plist at `/opt/homebrew/Cellar/ollama/*/homebrew.mxcl.ollama.plist`, since `brew
  services` doesn't expose a flag for this) → `/Volumes/Samsung870EVO/dev-storage/ollama-models`.
- Docker Desktop's VM disk (`DataFolder` key in `~/Library/Group Containers/group.com.docker/
  settings-store.json`, set *before* first real container pull so Docker never allocated its
  default-location image) → `/Volumes/Samsung870EVO/dev-storage/docker-data`. Confirmed via `docker
  info` → `Docker Root Dir` and by checking the actual (not sparse-file-apparent) size of
  `Docker.raw` with `du`, not `ls`.
- `uv` and Homebrew's download caches (`UV_CACHE_DIR`, `HOMEBREW_CACHE` in `~/.zshrc`) → same
  external drive, plus `brew cleanup -s` to reclaim ~700MB of already-downloaded bottles/casks
  immediately. Net effect: local disk went from 2.7GB → 5.5GB free and stayed flat through the
  entire Docker build + image pull + verification run.

**Four real bugs found by running against live infrastructure that the Phase 1 test suite (unit
tests + SQLite/fakeredis/MockProvider) structurally could not have caught:**

1. **Docker build failed outright**: `pyproject.toml` declares `readme = "README.md"` (needed by
   hatchling to build the `aurora` package), but neither `docker/Dockerfile.api` nor
   `Dockerfile.worker` copied `README.md` into the build context before `uv sync --no-dev`. Fixed
   by adding it to the `COPY pyproject.toml uv.lock* README.md ./` line in both.
2. **The worker never actually ran any task.** `celery -A worker.celery_app worker` only imports
   `worker/celery_app.py` — it never imports `worker/tasks.py`, so `run_inference` was never
   registered (`[tasks]` in the worker's own startup log was empty). Every dispatched task would
   have failed with "Received unregistered task of type...". Fixed by adding
   `imports=("worker.tasks",)` to `celery_app.conf`. While fixing this, also restructured
   `app/services/queue_service.py` to build its own minimal Celery producer client
   (`Celery("aurora-producer", broker=...)`) instead of importing `worker.celery_app` — the API
   image doesn't need the `worker/` package on disk at all now, which is a tighter version of the
   module-boundary rule in `ARCHITECTURE.md §6` than what was there before.
3. **Token usage was silently always 0.** Confirmed empirically against a live Ollama instance:
   its streamed chunks carry no `usage` object at all unless the request sets
   `stream_options: {"include_usage": true}` — and even then, usage arrives as a *separate final
   chunk with an empty `choices` array*, which the original `OllamaProvider.chat()` would have
   crashed on (`chunk["choices"][0]` on `[]`). Fixed in `providers/ollama_provider.py`, and
   hardened `worker/tasks.py` so a later `finished=True` chunk without usage data can't overwrite
   real numbers an earlier chunk already set.
4. **Assistant replies silently stopped persisting to Postgres after the first request** —
   `RuntimeError: Future ... attached to a different loop`. Root cause: `app/core/db.py`'s async
   engine (and `app/core/redis.py`'s async Redis client) are process-wide singletons, correct for
   `app/` (one Uvicorn process, one event loop, whole lifetime) but wrong for `worker/tasks.py`,
   which calls `asyncio.run()` fresh for *every task* — asyncpg/redis-py's async connections are
   bound to the loop that created them, so reusing the shared singleton's pooled connections across
   separate `asyncio.run()` calls broke on the second task. Fixed by having
   `_persist_assistant_message` open its own short-lived Redis client and `await
   get_engine().dispose()` after each use, so the next task's `asyncio.run()` call always gets
   connections bound to *its own* loop. Verified by firing 3 sequential real requests and confirming
   all 3 assistant replies landed in Postgres with no errors in the worker log.

**Two non-bug polish fixes made while verifying:** `uv run` was re-syncing (and reinstalling dev
dependencies like `mypy`/`ruff`) on every container start because `--no-sync` wasn't passed to the
runtime `CMD` — fixed in both Dockerfiles and the `flower` command in `docker-compose.yml`. Celery
redirects worker stdout through its own logging by default, tagging every captured line `WARNING`
regardless of content, which stomped on structlog's own `level` field in the JSON payload — fixed
with `worker_redirect_stdouts=False`.

**End-to-end verification, all against the real stack (Docker Compose + host Ollama serving
`llama3.2:3b`):**
- `alembic upgrade head` against real Postgres — schema matches `DATA_MODEL.md §1` exactly (checked
  via `\d messages` in `psql`).
- `POST /chat` with `X-API-Key` auth streams real token-by-token SSE output from Ollama through the
  full API → Redis pub/sub → Celery worker → Ollama pipeline, terminating in `event: done` with
  real `promptTokens`/`completionTokens`/`latencyMs`.
- Multi-turn context works: a follow-up message in the same conversation correctly referenced the
  prior turn, proving the Redis session cache + Postgres fallback in `session_service.get_context`
  actually round-trips real conversation history through a real model.
- `GET /health` reports `healthy` with real per-dependency latencies (Postgres 12.7ms, Redis
  0.7ms, inference 71.8ms) when everything is up.
- Rate limiting: temporarily lowered `RATE_LIMIT_REQUESTS` to 3, confirmed real `429` responses
  with `Retry-After` and the exact `API_SPEC.md` error body, then restored the normal value.
- `GET /metrics` shows real, non-zero values for all 7 required metrics after live traffic,
  including `aurora_ttft_seconds` (~0.2s) and `aurora_tpot_seconds` (~12ms/token) computed from an
  actual model generation, not synthetic data.
- Flower (`:5555`) reachable and live.

**Phase 1 is now genuinely done per `ROADMAP.md`'s definition** — not just code passing against
test doubles, but demonstrated working against the real dependencies it was designed for. Local
test suite (39 tests), `ruff`, and `mypy` all still pass after every fix above.

---

## Issues encountered and how they were resolved — consolidated (2026-07-14)

The narrative entries above are in build order; this section pulls every problem hit during the
day into one place, grouped by how it was found, since that's the more useful axis for judging
what a given layer of verification (lint → type-check → unit tests → live infra) actually catches.
Each is **Problem → Cause → Fix**.

### A. Environment / process problems

1. **Machine had no Python 3.12, `uv`, Docker, or Ollama.**
   *Cause*: fresh dev environment, Phase 0 was design-only (no code, no tooling needed yet).
   *Fix*: installed `uv` + Python 3.12.13 via Homebrew immediately (low-risk, CLI-only). Held off
   on Docker/Ollama and asked the user first, since those are heavier (GUI app, multi-GB model
   download) — proceeded once explicitly confirmed.
2. **Two `AskUserQuestion` prompts about environment setup went unanswered** (60s timeout, no
   response). *Fix*: did not assume consent for the higher-stakes installs; proceeded only with
   the lightweight, easily-reversible option (`uv`/Python) and reported the blocker back in plain
   language instead of guessing or retrying indefinitely. Waited for an explicit "yes, install
   both" before touching Docker/Ollama.
3. **Local disk had only ~2.7GB free** — discovered only after starting the Docker/Ollama install,
   not before. Not nearly enough for Docker's VM disk image plus Postgres/Redis/api/worker images.
   *Cause*: unrelated large caches already on the machine (Spotify, Yarn, various apps' ShipIt
   caches) plus no prior externalization of dev tooling storage.
   *Fix*: user flagged this mid-task and asked for downloads to go to an external drive instead.
   Relocated Ollama's model directory (`OLLAMA_MODELS` via the brew-services launchd plist),
   Docker Desktop's entire VM/data folder (`DataFolder` key in `settings-store.json`, set *before*
   Docker had allocated its default-location disk image, so nothing needed migrating after the
   fact), and the `uv`/Homebrew caches, all to `/Volumes/Samsung870EVO`. Also ran `brew cleanup -s`
   to reclaim ~700MB immediately. Net: 2.7GB → 5.5GB free locally, flat through the entire
   build+verify run.
4. **`brew install --cask docker` failed**: "It seems there is already an App at
   '/Applications/Docker.app'." *Cause*: Docker Desktop had been installed manually (not via
   Homebrew) back in June, so the cask install collided with the existing app.
   *Fix*: didn't fight the cask installer — checked whether the existing install worked
   (`/Applications/Docker.app/Contents/Resources/bin/docker --version` succeeded), then just
   launched and used it directly rather than forcing a reinstall.

### B. Bugs caught by static analysis (ruff / mypy), before any test ran

5. **`ModelProvider.chat` typed `async def` in the Protocol** (`providers/base.py`), matching
   `ARCHITECTURE.md §4`'s illustrative pseudocode. *Cause*: real implementations are async
   *generator* functions (`async def chat(...): yield ...`) — calling one returns an
   `AsyncIterator` synchronously; it's never itself awaited. `async def` in the Protocol told mypy
   to expect `await provider.chat(...)`, which every concrete implementation violated.
   *Fix*: changed the Protocol method to plain `def` returning `AsyncIterator[ChatChunk]`.
6. **Assorted mypy friction**, each a known stub/typing gap rather than a real bug: redis-py's
   async `llen()` typed as `Awaitable[int] | int` (a stub quirk) triggering `await`-type errors in
   `queue_service.py` and `health.py` — resolved with a targeted `# type: ignore[misc]` and a
   comment explaining why. `Conversation.__table__.update()` — SQLAlchemy types `__table__` as the
   looser `FromClause`, not `Table` — resolved by switching to the 2.0-style
   `update(Conversation)...` construct instead of touching `__table__` directly. `SSEEvent.data`'s
   union type (`TokenEventData | DoneEventData | ErrorEventData`) couldn't be narrowed by comparing
   `event.event == "done"` as a string — resolved with `isinstance(event.data, DoneEventData)`
   checks instead, which mypy *can* narrow on.

### C. Bugs caught by the unit/integration test suite (SQLite + fakeredis + MockProvider)

7. **`IntegrityError: NOT NULL constraint failed` on the second message in any conversation.**
   *Cause*: `session_service.append_message` set `conversations.updated_at = message.created_at`,
   but `message.created_at` is `None` in Python until the row is actually flushed (it's a
   `server_default=func.now()` column) — read before it existed.
   *Fix*: used `func.now()` directly in the `UPDATE` statement instead of the ORM object's
   unrefreshed attribute.
8. **A request cancelled before any token streamed was misreported as `status="success"`.**
   *Cause*: the cancellation check lived inside the `async for` loop body in
   `worker/tasks.py:_run_inference_async`, but a provider whose generator honors `cancel_check`
   internally (both `MockProvider` and `OllamaProvider` do) can return with **zero** iterations —
   so that check never ran, and the function fell through to its default "success" return.
   *Fix*: added one more `cancel_check()` after the loop, before declaring success. Caught by
   `test_cancellation_stops_the_stream_without_publishing_a_terminal_event`.
9. **My own test fixture, not application code, had a bug**: `fake_dispatch` in `tests/conftest.py`
   simulated the worker's pub/sub publishing but not its Postgres persistence step, so tests
   asserting on persisted assistant messages failed with `StopIteration` (empty generator).
   *Cause*: persistence is genuinely the worker's job (`SEQUENCE_DIAGRAMS.md §1`), and the fake
   only covered half of it. *Fix*: extended the fixture to also call
   `session_service.append_message`, mirroring what `worker/tasks.py` really does — and ordered it
   *before* publishing the `done` event (deliberately more deterministic than the real system,
   which persists *after* publishing `done` — a documented, accepted race, see decision list above).

### D. Bugs that only running the real stack (Docker Compose + live Postgres/Redis/Ollama) caught

None of these four were reachable by A–C above; each needed an actual container boot, a real
Celery worker process, or a real Ollama response shape.

10. **Docker build failed outright**: `uv sync --no-dev` errored with `OSError: Readme file does
    not exist: README.md`. *Cause*: `pyproject.toml` declares `readme = "README.md"` for hatchling,
    but neither Dockerfile copied it into the build context.
    *Fix*: added `README.md` to the `COPY pyproject.toml uv.lock* README.md ./` line in both
    `Dockerfile.api` and `Dockerfile.worker`.
11. **The worker container started cleanly but never ran a single task.** `[tasks]` in its own
    startup log was empty. *Cause*: `celery -A worker.celery_app worker` only imports
    `worker/celery_app.py` — Celery does not automatically import `worker/tasks.py` just because
    it's in the same package, so `run_inference` was never registered; every dispatched task would
    have failed with "Received unregistered task of type...".
    *Fix*: added `imports=("worker.tasks",)` to `celery_app.conf`. While fixing this, also
    restructured `app/services/queue_service.py` to build its own minimal Celery producer client
    instead of importing `worker.celery_app` at all, so the API image no longer needs the
    `worker/` package on disk — a stricter version of the module-boundary rule than before.
12. **Token usage (`promptTokens`/`completionTokens`) was silently always `0` end-to-end.**
    *Cause*: confirmed empirically against a live Ollama instance — its streamed chunks carry no
    `usage` object at all unless the request sets `stream_options: {"include_usage": true}`, and
    even then usage arrives as a *separate final chunk with an empty `choices` array*, which the
    original `OllamaProvider.chat()` would have crashed on (`chunk["choices"][0]` on `[]`) had a
    caller ever triggered that path.
    *Fix*: added `stream_options.include_usage` to the request payload; handled the empty-`choices`
    usage-only chunk as its own case; hardened `worker/tasks.py` so a later `finished=True` chunk
    without usage numbers can't overwrite real numbers an earlier chunk already set.
13. **Assistant replies stopped persisting to Postgres after the very first request** —
    `RuntimeError: Future ... attached to a different loop`, only on the second+ chat request in a
    freshly started worker. *Cause*: `app/core/db.py`'s async engine and `app/core/redis.py`'s
    async Redis client are process-wide singletons — correct for `app/` (one Uvicorn process, one
    event loop, whole process lifetime) but wrong for `worker/tasks.py`, which calls `asyncio.run()`
    fresh for *every task*. asyncpg/redis-py's async connections are bound to the event loop that
    created them, so the second task's `asyncio.run()` call reused pooled connections still tied to
    the *first* task's already-closed loop.
    *Fix*: `_persist_assistant_message` now opens its own short-lived Redis client and calls
    `await get_engine().dispose()` after each use, forcing the next task to open fresh,
    correctly-loop-bound connections. Verified by firing 3 sequential real requests with zero
    errors, where the unfixed version failed on request 2.

### E. Non-bug polish found while verifying (not correctness issues, but worth fixing)

14. **Every container re-synced its Python environment on every start**, downloading `mypy`/`ruff`
    (dev-only deps) despite the image being built with `--no-dev`. *Cause*: `uv run` re-resolves
    and syncs by default at run time regardless of what flags were used at build time.
    *Fix*: added `--no-sync` to the `CMD`/`command` in both Dockerfiles and the `flower` service.
15. **Structured JSON log lines from the worker all showed up tagged `WARNING`** regardless of
    their actual `"level"` field. *Cause*: Celery redirects worker stdout/stderr through its own
    logging by default, and treats anything it captures that isn't from its own logger as
    `WARNING`. *Fix*: `worker_redirect_stdouts=False` in `celery_app.conf`, letting structlog's
    already-leveled JSON lines pass through untouched.

## 2026-10-11

**Rate limiter made atomic.** `app/core/rate_limit.py` issued `ZREMRANGEBYSCORE`, `ZCARD` and `ZADD`
as separate commands, so concurrent requests could all read `count < limit` before any of them had
added its entry.

*Evidence, against a real Redis 7 (60 concurrent requests, limit 5)*: the old code allowed **60**;
the Lua version allowed **5**. The old run also left `zcard=55`, a second bug: the sorted-set member
was the bare timestamp, so requests in the same clock tick collapsed into one entry and were
under-counted.

*Fix*: the whole decision is one Lua script (`EVAL`), and each member is `timestamp:uuid`.

*Test*: `test_concurrent_requests_never_exceed_the_limit`. fakeredis completes each command without
yielding to the event loop, which hides the race (the same test passed on the old code), so the test
wraps the client to yield before every command, as a network round trip does. It fails on the old
code (30 allowed, limit 5). Added `fakeredis[lua]` (`lupa`) as a dev dependency so fakeredis can run
Lua. 40 tests, ruff and mypy clean.

*Known limit*: the timestamp still comes from the API process, so clock skew between several API
instances shifts window edges. Using Redis `TIME` inside the script would remove that.

**Startup configuration hardened (security and config behaviour change).** Found in an external review
of the rate-limiter change; the issues predate it.

1. **A publicly known JWT signing key could run in production.** `jwt_secret` defaulted to
   `change-me-in-real-env`, which is also in `.env.example` and in this public repo, and nothing stopped
   a production process from using it (PyJWT already warned that the key was 21 bytes). Anyone could then
   mint valid tokens. *Fix*: with `ENVIRONMENT=production`, `Settings` refuses a secret that starts with
   `change-me` or is shorter than 32 bytes (bytes, not characters). Development and test are unchanged.
2. **The rate limit could be switched off by a typo.** `RATE_LIMIT_WINDOW_SECONDS=0` made the Lua script
   `EXPIRE` the key immediately, so every request was allowed; `RATE_LIMIT_REQUESTS=0` rejected
   everything. Neither was validated, although `config.py` claims invalid settings refuse to boot.
   *Fix*: both fields are `Field(gt=0)` in every environment.
3. **`Retry-After` was rounded down** (`int()`), so 59.6 s left was reported as 59 and a client that
   waited exactly as told could be rejected again. *Fix*: `math.ceil()`.
4. **Validation errors could echo the secret.** Without `hide_input_in_errors`, pydantic prints the
   input as `head...tail`, which leaks the end of a rejected secret into the logs. *Fix*: the option is
   set on `Settings`. A first version of the test asserted that the whole secret was absent and passed
   even without the option, because of that truncation; it now checks a distinctive tail, and fails
   when the option is removed.

*Tests*: `tests/unit/test_config.py` (new) and one `Retry-After` test. The configuration tests run
both with keyword arguments and through real environment variables followed by a bare `Settings()`,
which is the path a deployed process takes. The refusal tests fail on the previous code. 60 tests,
ruff and mypy clean.

*Behaviour change for operators*: a production deployment that has not set a real `JWT_SECRET` of at
least 32 bytes now fails at start-up instead of running. Not exercised through Docker Compose: no
Compose service sets `ENVIRONMENT=production` today.
