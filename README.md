# Aurora

[![CI](https://github.com/Maaayhan/Aurora/actions/workflows/ci.yml/badge.svg)](https://github.com/Maaayhan/Aurora/actions/workflows/ci.yml)

**A production-grade backend platform that serves open-source large language models through reliable APIs — streaming responses, session management, queuing, observability, and deployment workflows.**

Aurora is not a chatbot. It's the platform a chatbot runs on.

## The premise

Aurora AI's research team spent six months training a model, `Aurora-13B`. Training is done. The GPU cluster is up. But a checkpoint is not a service — there is no API, no auth, no rate limiting, no retry logic, no way to know if the model is even healthy right now. This project is the platform that turns a trained checkpoint into something the public can depend on.

Locally, `Aurora-13B` is stood in by a small open-source model served through [Ollama](https://ollama.com)'s OpenAI-compatible API — the inference layer is written against an interface, not a specific runtime, so the model underneath is swappable without touching application code.

## Why this project exists

This is portfolio work built to demonstrate production backend engineering for LLM-serving systems: request/session handling, reliability mechanisms (rate limiting, retries, circuit breakers, backpressure), observability, and deployment design — the concerns of an **AI platform team**, not an AI application team. There is deliberately no business logic, no RAG, no agents, no tool calling anywhere in this codebase. See [`docs/PRD.md`](docs/PRD.md) for the explicit non-goals and why.

## Documentation

Design comes before code. Start here:

| Doc | Contents |
|---|---|
| [`docs/PRD.md`](docs/PRD.md) | Problem statement, goals/non-goals, phased scope, success criteria |
| [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) | System layers, module boundaries, tech choices and why |
| [`docs/DATA_MODEL.md`](docs/DATA_MODEL.md) | Postgres schema, Redis key design, queue job shape |
| [`docs/API_SPEC.md`](docs/API_SPEC.md) | Endpoints, request/response contracts, SSE event format, error codes |
| [`docs/SEQUENCE_DIAGRAMS.md`](docs/SEQUENCE_DIAGRAMS.md) | Request flow, streaming, cancellation, retry, deployment — as Mermaid diagrams |
| [`docs/ERROR_HANDLING.md`](docs/ERROR_HANDLING.md) | Every failure mode this system is designed to survive, and how |
| [`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md) | Docker Compose topology, checkpoint promotion pipeline, rollback |
| [`docs/ROADMAP.md`](docs/ROADMAP.md) | Phased build plan (MVP → Production → Observability → Deployment) with a definition of done per phase |

## Status

Phase 1 (MVP) is implemented: `POST /chat` with SSE token streaming, JWT / API-key auth,
Redis sliding-window rate limiting, Postgres-backed conversations, a Celery inference worker with
retry-and-backoff and cooperative cancellation, structured JSON logs with request IDs, and Prometheus
metrics including TTFT / TPOT. The 60 automated tests run against `MockProvider`, SQLite and
fakeredis; the full stack (Docker Compose, Postgres, Redis, a local Ollama model) was exercised
end to end by hand, which found four bugs the tests could not (see `docs/DEVLOG.md`, 2026-07-14).

Not built yet: circuit breaker (Phase 2), Grafana / Jaeger wiring (Phase 3), `scripts/deploy.py` /
`rollback.py` and a vLLM provider (Phase 4). `docs/ROADMAP.md` still has its Phase 0 wording, and
`docs/DEVLOG.md` records what was built, the bugs found while verifying, and the deviations from the design.

## Stack

Python 3.12 · FastAPI · Celery (Redis broker) · PostgreSQL (SQLAlchemy + Alembic) · Redis · Ollama (local, native — Metal) / vLLM (future production) behind a shared provider interface · Docker Compose · Prometheus/Grafana · OpenTelemetry/Jaeger · Flower

## Repo layout

```
app/          FastAPI service — the API layer (auth, rate limiting, routes, SSE)
worker/       Celery worker — the inference layer (retry with backoff, cancellation, streaming relay; circuit breaker not yet built)
providers/    ModelProvider interface + OllamaProvider / MockProvider (vLLM planned)
shared/       Pydantic models shared across app/ and worker/ (task payload, SSE events, error codes)
migrations/   Alembic schema migrations
docker/       Docker Compose + service configs
docs/         Everything in the table above
scripts/      create_user.py (deploy / rollback / load-test scripts are planned)
tests/
```
