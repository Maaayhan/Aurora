# Aurora

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

Design phase. No application code yet — see `docs/ROADMAP.md` for why that's intentional and what unlocks Phase 1 (code).

## Stack

Python 3.12 · FastAPI · Celery (Redis broker) · PostgreSQL (SQLAlchemy + Alembic) · Redis · Ollama (local, native — Metal) / vLLM (future production) behind a shared provider interface · Docker Compose · Prometheus/Grafana · OpenTelemetry/Jaeger · Flower

## Repo layout

```
app/          FastAPI service — the API layer (auth, rate limiting, routes, SSE)
worker/       Celery worker — the inference layer (retry, circuit breaker, streaming relay)
providers/    ModelProvider interface + OllamaProvider / VLLMProvider / MockProvider
shared/       Pydantic models shared across app/ and worker/ (task payload, SSE events, error codes)
migrations/   Alembic schema migrations
docker/       Docker Compose + service configs
docs/         Everything in the table above
scripts/      deploy.py / rollback.py, load-test scripts
tests/
```
