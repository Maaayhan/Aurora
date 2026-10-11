# Aurora — Kanban

Working board for implementation. One card per unit of demonstrable work. Phases match `ROADMAP.md`; a phase does not start until the previous phase's definition of done is real (`ROADMAP.md` "Non-negotiable ordering rule").

Update this file as work moves — don't let it drift from `DEVLOG.md`. When a card moves to **Done**, it should have a matching entry in `DEVLOG.md` with the reasoning behind any non-obvious call made while building it.

Legend: 🟦 Backlog · 🟨 In Progress · 🟩 Done · ⛔ Blocked

---

## Phase 0 — Design

| Card | Status |
|---|---|
| All `docs/` written and internally consistent | 🟩 Done |
| Repo skeleton (`app/`, `worker/`, `providers/`, `shared/`, `docker/`, `scripts/`) | 🟩 Done |
| Project tracking: this kanban + `DEVLOG.md` | 🟩 Done |

## Phase 1 — MVP

**Definition of done** (`ROADMAP.md`): client can `POST /chat`, auth via JWT or API key, token-by-token SSE stream, persisted conversation, `429` past rate limit threshold, failed inference retried with backoff, every request logged as structured JSON with `request_id`/latency/status, `/health` reports real dependency status, 7 Prometheus metrics exposed.

| Card | Status | Notes |
|---|---|---|
| Project scaffolding (`pyproject.toml`, config, `.env.example`, `docker-compose.yml`) | 🟩 Done | uv + Python 3.12 toolchain installed locally |
| `shared/` models — `ChatMessage`, SSE event shapes, error code enum, `InferenceTaskPayload` | 🟩 Done | |
| `providers/base.py` (`ModelProvider` Protocol) | 🟩 Done | |
| `providers/ollama_provider.py` | 🟩 Done | verified against live Ollama; fixed a real `usage`-parsing bug (`DEVLOG.md` 2026-07-14) |
| `providers/mock_provider.py` | 🟩 Done | |
| Postgres models (`users`, `conversations`, `messages`, `feedback`, `model_deployments`) | 🟩 Done | verified against real Postgres via `psql \d` |
| Alembic migration setup + initial migration | 🟩 Done | ran clean against real Postgres (`alembic upgrade head`) |
| `app/core/config.py` (pydantic-settings, shared by app/worker) | 🟩 Done | |
| `app/core/auth.py` (JWT + API key) | 🟩 Done | |
| `app/core/rate_limit.py` (sliding window log) | 🟩 Done | live `429` + `Retry-After` confirmed against real Redis |
| `app/services/session_service.py` | 🟩 Done | multi-turn context confirmed against a real model |
| `app/services/queue_service.py` (dispatch + pub/sub bridge) | 🟩 Done | refactored to its own Celery producer client, not an import of `worker.celery_app` |
| `worker/celery_app.py` (`task_acks_late`, `task_reject_on_worker_lost`) | 🟩 Done | fixed missing `imports=("worker.tasks",)` — task was never registered without it |
| `worker/tasks.py` (`run_inference`) | 🟩 Done | fixed a cross-event-loop Postgres/Redis bug found via live multi-request testing |
| `worker/reliability.py` (retry-with-backoff, cancellation poll; no circuit breaker yet) | 🟩 Done | |
| `app/api/chat.py` (`POST /chat`, SSE) | 🟩 Done | real end-to-end streaming confirmed against live Ollama |
| `app/api/auth.py` (`POST /auth/token`, dev-only key exchange) | 🟩 Done | not in original API_SPEC.md table — fills the doc's own "not detailed further" gap |
| `app/api/conversations.py` (`GET /conversations`, `GET /conversations/:id`) | 🟩 Done | |
| `app/api/feedback.py` (`POST /messages/:id/feedback`) | 🟩 Done | |
| `app/api/health.py` (`GET /health`) | 🟩 Done | confirmed `healthy`/`degraded`/`unhealthy` all report correctly |
| Prometheus metrics (7 required, `GET /metrics`) | 🟩 Done | confirmed non-zero real values (TTFT/TPOT) after live traffic |
| `structlog` wiring (app + worker, JSON stdout, `request_id` on every line) | 🟩 Done | fixed Celery stomping the JSON `level` field (`worker_redirect_stdouts=False`) |
| Docker Compose: `api`, `worker`, `postgres`, `redis`, `flower` | 🟩 Done | full stack up and healthy; fixed missing `README.md` in both Dockerfiles |
| Tests: auth, rate limit, chat flow (via `MockProvider`), health | 🟩 Done | 60 tests, ruff clean, mypy clean |
| End-to-end verification against local Ollama | 🟩 Done | see `DEVLOG.md` 2026-07-14 for the full run and the 4 real bugs it caught |

**Phase 1 status: DONE, for real** — code-complete and verified against the actual dependencies it was designed for (live Postgres, Redis, Ollama, Docker Compose), per `ROADMAP.md`'s own standard ("demonstrable, not just 'code exists'"). Docker Desktop's data and Ollama's models were relocated to an external drive (`/Volumes/Samsung870EVO`) since local disk only had ~2.7GB free — see `DEVLOG.md` for the setup.

## Phase 2 — Production hardening

| Card | Status |
|---|---|
| Circuit breaker (`ERROR_HANDLING.md` §4) | 🟦 Backlog |
| Backpressure rejection at API (`SERVER_BUSY`) | 🟦 Backlog |
| Cooperative cancellation propagation, end-to-end | 🟦 Backlog |
| Per-attempt vs per-task timeout distinction | 🟦 Backlog |
| Demonstrate: kill Ollama mid-stream → clean `error` event | 🟦 Backlog |
| Demonstrate: 50 concurrent requests vs `--concurrency=8` → queueing | 🟦 Backlog |
| Demonstrate: client disconnect → worker actually stops (log proof) | 🟦 Backlog |
| Demonstrate: kill worker mid-task → another worker picks it up (Flower) | 🟦 Backlog |

## Phase 3 — Observability

| Card | Status |
|---|---|
| OpenTelemetry instrumentation across API→queue→worker | 🟦 Backlog |
| Jaeger collector wired in Compose | 🟦 Backlog |
| Grafana dashboard provisioned as code | 🟦 Backlog |
| `k6` load-test script | 🟦 Backlog |
| Demonstrate: trace a single `request_id` end-to-end in Jaeger | 🟦 Backlog |

## Phase 4 — Deployment

| Card | Status |
|---|---|
| `scripts/deploy.py` — register → warm-up → smoke-test | 🟦 Backlog |
| `scripts/rollback.py` | 🟦 Backlog |
| Canary weighting (stretch — may remain "designed, not automated") | 🟦 Backlog |
| Automated rollback triggers (stretch) | 🟦 Backlog |

## Bonus scope (not before Phase 1–2 are solid)

| Card | Status |
|---|---|
| Multi-model routing | 🟦 Backlog |
| Model versioning / A-B testing | 🟦 Backlog |
| Shadow deployment | 🟦 Backlog |
| Token-usage / cost accounting | 🟦 Backlog |
