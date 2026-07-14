# Aurora — Product Requirements Document

## 1. Problem statement

A trained model checkpoint is not a product. Between "the weights exist" and "a user can reliably talk to this model" sits an entire platform: something has to authenticate the caller, decide whether to accept the request right now, hold it in a queue if the model is busy, stream tokens back as they're generated, know when to give up and retry, know when to stop retrying and fail loudly instead of quietly, and record enough about every request that an engineer can reconstruct what happened six hours later when something goes wrong at 2am.

Aurora is that platform. It sits between "Aurora AI's research team has a checkpoint" and "a user can send it a message and get a reliable response" — nothing more, nothing less.

## 2. Goals

- Serve a locally-hosted open-source LLM through a stable, versioned HTTP API.
- Stream model output token-by-token over SSE, not as a single blocking response.
- Track conversations as first-class objects (session, history, timestamps), not stateless one-shot calls.
- Make every failure mode explicit and handled: auth failure, rate limit, timeout, inference error, queue saturation, model unavailable.
- Make the system's internal state observable from the outside: logs, metrics, traces, health checks — without reading application code.
- Design (not necessarily fully implement) a checkpoint promotion pipeline: how a new model version goes from "research produced it" to "serving production traffic" safely, with a rollback path.

## 3. Non-goals

Aurora deliberately does not include:

- **Any business domain logic.** No CRM, no hospital records, no invoicing, no customer-specific workflows. Nothing here models a business problem — it models a serving problem.
- **RAG, agents, or tool-calling.** These are AI *application* concerns — they live on top of a model API, not inside the platform that serves the model. Conflating them would blur exactly the distinction this project exists to demonstrate.
- **Model training or fine-tuning.** Aurora consumes a checkpoint; it does not produce one.
- **A polished chat UI.** A minimal client (curl scripts / a bare test page) is enough to exercise streaming. This is a backend platform project — UI is not where the engineering is.
- **Calling a hosted third-party model API (OpenAI, Anthropic, etc.) as the actual inference backend.** The application code talks to the OpenAI-compatible *protocol* only as a wire format — the model actually answering the request runs locally.

## 4. Users

There is exactly one external actor: **a client application** that sends chat requests and consumes a streamed response. There is no end-user-facing product decision to make (no onboarding flow, no billing, no multi-tenant admin console) — the "user" of this system, in the sense that matters for design decisions, is the *next engineer* who has to operate it, extend it, or debug it under load.

## 5. Scope, phased

Full detail and definition-of-done per phase lives in [`ROADMAP.md`](ROADMAP.md). Summary:

| Phase | Theme | Answers |
|---|---|---|
| 0 | Design | Is the system specified precisely enough to build without re-deciding things mid-implementation? |
| 1 | MVP | Can a client send a message and reliably stream back a response, with auth, rate limiting, retry, and logging in place? |
| 2 | Production hardening | Does the system survive concurrency pressure — queueing, backpressure, circuit breaking, cancellation, timeouts — without falling over or silently corrupting state? |
| 3 | Observability | Can an operator answer "what is this system doing right now, and why did that one request fail" without reading source code? |
| 4 | Deployment | Is there a designed (if not fully automated) path from "research produced checkpoint-v17" to "v17 is serving traffic," with a rollback? |

## 6. Success criteria

Aurora is "done" for portfolio purposes when:

1. A cold read of `docs/` alone (no code) gives an engineer a precise enough picture of the system that they could implement it themselves and land close to the actual implementation.
2. Every reliability mechanism named in the Maincode JD — rate limiting, retries, graceful failure, auth, streaming, logging/telemetry — exists in the code, not just in the docs, and can be demonstrated failing and recovering on demand (e.g., "watch what happens when I kill the inference container mid-stream").
3. The system can be explained end-to-end, out loud, from a cold start, in the format of a real design review — not recited from memory, but reasoned through, including the tradeoffs that were rejected.

## 7. Explicit tradeoffs and why

These are the calls a reviewer is most likely to probe — stated up front rather than left implicit:

- **Python/FastAPI over NestJS/TypeScript.** This is a deliberate choice *against* the author's language of deepest production depth. Two reasons override that instinct: (1) the real LLM-serving ecosystem — vLLM, TGI, Ray Serve, Triton, and Maincode's own Rust-engine-with-a-Python-service-layer-above-it shape — is overwhelmingly Python, so a Python implementation looks like what the target system actually is, not an adjacent skill transposed sideways; (2) it's the honest way to close a real, named gap rather than avoid it — building this in a language already fluent in TypeScript would produce a well-designed project that proves nothing new.
- **Ollama over vLLM, for local development only.** vLLM's PagedAttention/continuous-batching engine targets Linux + NVIDIA CUDA; it does not run natively on macOS. Ollama runs locally on Apple Silicon via Metal and exposes an OpenAI-compatible `/v1/chat/completions` endpoint, which is the only contract the application layer is allowed to depend on. `VLLMProvider` implements the same interface for a real GPU deployment later — swapping providers is a config change, not a rewrite. That's the point of the boundary in `ARCHITECTURE.md` §4.
- **Celery over a hand-rolled asyncio queue or `arq`.** Celery is the mature, batteries-included choice in the Python ecosystem — but its defaults are tuned for fire-and-forget background jobs, not for a long-running task whose partial output streams out live. Two settings (`task_acks_late`, `task_reject_on_worker_lost`) have to be turned on deliberately to get BullMQ-equivalent crash-safety; this is treated as a named risk, not an assumption — see `ERROR_HANDLING.md` §6.
- **A worker process separate from the API**, connected via Celery + Redis pub/sub, rather than the API calling the model directly inside a request handler. This is added because it's what makes the queue/concurrency-limiting requirement real instead of decorative. See `ARCHITECTURE.md` §3 for the reasoning.
- **Ollama runs on the host, not in Docker**, because Docker Desktop on macOS doesn't pass Metal through to Linux containers — containerizing it would silently make every latency number in this project meaningless. See `DEPLOYMENT.md` §1.
