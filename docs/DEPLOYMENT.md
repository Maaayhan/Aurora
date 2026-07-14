# Aurora — Deployment

## 1. Local topology (Docker Compose)

```yaml
services:
  api:          # app/ — FastAPI under Uvicorn, HTTP/SSE, no model-bound work
  worker:       # worker/ — Celery consumer, talks to the model provider
  postgres:     # system of record
  redis:        # sessions, rate limiting, Celery broker, pub/sub
  flower:       # Celery queue/worker dashboard
  prometheus:   # scrapes /metrics from api + worker (Phase 3)
  grafana:      # dashboards over prometheus (Phase 3)
  jaeger:       # trace collector (Phase 3)
```

**Ollama runs natively on the Mac mini host, not in Docker.** Docker Desktop on macOS does not pass Apple Silicon's Metal GPU through to Linux containers — an Ollama container would silently fall back to CPU-only inference, which is dramatically slower and would make every latency number in this project meaningless. Ollama is installed and run directly on the host; `api` and `worker` reach it at `http://host.docker.internal:11434`, configured via the same `MODEL_PROVIDER_URL` env var that would point at a real vLLM endpoint in production — the containers never know or care that the target is off-container. This is a Mac-development-specific detail, not a permanent architectural constraint: on a real GPU host running vLLM, the model service is containerized normally (GPU passthrough on Linux + NVIDIA works as expected via the NVIDIA Container Toolkit).

`worker` scales independently: `docker compose up --scale worker=3` is the local equivalent of "add another inference-capacity replica" — Celery's `--concurrency` flag on each worker further caps how many requests that replica runs at once. The API doesn't need to know this happened, since coordination is entirely through Redis (this is the payoff of the split argued for in `ARCHITECTURE.md` §3).

`api` and `worker` both read a single `.env` (`.env.example` checked in, real `.env` gitignored) so environment configuration — timeouts, retry counts, rate limit numbers, `MODEL_PROVIDER` — is defined once and consistent across processes, per the spec's "environment: unified" requirement.

## 2. Checkpoint promotion pipeline

The scenario this answers: research finishes training `checkpoint-v17`. How does it become the model actually answering `/chat` requests, safely?

```
checkpoint produced
      │
      ▼
1. Register            model_deployments row created, status = registered
      │
      ▼
2. Warm up              new worker/model instance started, 0% of traffic — the
      │                 provider pulls/loads the checkpoint, first inference call
      │                 absorbs cold-start latency before any real user sees it
      ▼
3. Health check         smoke-test prompts run against the warm instance directly
      │                 (bypassing the router) — checks it responds at all and
      │                 within a latency budget
      ▼
4. Canary               traffic router sends a small % (default 10%) of real
      │                 requests to v17, rest continue to the previous active
      │                 version. Error rate / latency for the canary slice is
      │                 compared against baseline over a monitoring window.
      ▼
5a. Promote             canary healthy → traffic shifted to 100%, v17 marked
      │                 active, previous version's deployment row marked retired
      │                 (kept registered, not deleted — see rollback below)
      ▼
5b. Rollback            canary unhealthy → traffic shifted back to 100% previous
                         version immediately, v17 marked rolled_back. No manual
                         redeploy needed to recover — the previous version's
                         instance was never stopped during canary.
```

**Why the previous version stays running during canary**: rollback has to be a traffic-routing decision, not a redeploy — if reverting requires spinning the old version back up from cold, "rollback" has the same latency and failure surface as a fresh deploy, which defeats the purpose of having one. The routing layer (in Aurora's case: which `model` value a task payload carries, resolved by a lookup against `model_deployments.status`/`traffic_pct` at dispatch time) is the only thing that needs to change to roll back.

**What's actually implemented vs. designed**: for a portfolio-scale project, steps 1–3 are realistic to fully implement (a `scripts/deploy.py` that registers a checkpoint tag, starts a tagged Ollama model pull, runs a smoke-test script against it). Steps 4–5 (weighted canary routing, automated rollback triggers) are designed precisely enough to implement, but whether they're built out fully or left as a well-specified "next step" is a Phase 4 scoping call — see `ROADMAP.md`. The point of writing the full pipeline here regardless is that the design, not just the code, is what's being evaluated.

## 3. Configuration

Single source of truth: `.env` at the repo root, loaded via a typed `pydantic-settings` config object (`app/core/config.py`, imported by both `app/` and `worker/`) — validated at process start, so the process refuses to boot with an invalid/missing required var rather than failing later at first use. No magic numbers in code — every threshold named in `ERROR_HANDLING.md` (timeouts, retry counts, circuit breaker thresholds, rate limit numbers, queue depth threshold) is an env var with a documented default.

## 4. Logging

Structured JSON to stdout from both processes (`structlog`, collected by Docker Compose / whatever aggregates container logs). Every log line carries `request_id` when one exists, so a single request's path across API → queue → worker → model provider can be reconstructed by filtering on one field, even without a full tracing backend wired up. Phase 3 (OpenTelemetry + Jaeger) makes this reconstruction automatic instead of manual — but the manual version isn't a stopgap that gets thrown away, it's what a log-based fallback looks like when the tracing backend itself is unavailable.
