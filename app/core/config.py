"""Typed settings, single source of truth for both app/ and worker/ — DEPLOYMENT.md §3.

Validated at process start (pydantic-settings raises on missing/invalid
required vars), so a misconfigured process refuses to boot rather than
failing later at first use. Every threshold named in ERROR_HANDLING.md is an
env var with a documented default here — no magic numbers in code.
"""

from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    environment: Literal["development", "production", "test"] = "development"
    log_level: str = "INFO"

    # Postgres
    database_url: str = "postgresql+asyncpg://aurora:aurora@localhost:5432/aurora"

    # Redis
    redis_url: str = "redis://localhost:6379/0"

    # Celery
    celery_broker_url: str = "redis://localhost:6379/1"
    celery_result_backend: str = "redis://localhost:6379/2"
    celery_worker_concurrency: int = 4
    # How long an unacked task stays invisible to other workers after the worker holding it
    # disappears, before Redis (Celery's broker transport for Redis, specifically — not a
    # Celery-level setting) makes it visible for redelivery again — ERROR_HANDLING.md §6.
    # task_acks_late + task_reject_on_worker_lost (worker/celery_app.py) only cover the case
    # where Celery's own process monitor notices a *child* process died (e.g. one prefork worker
    # process crashes but the parent survives); if the *entire* worker process tree disappears
    # at once (container killed, host rebooted), nothing is left to explicitly requeue the
    # task — recovery depends entirely on this broker-level timeout. Redis transport's own
    # default is 3600s (1 hour), which is far too slow for a task whose whole point is
    # sub-minute inference latency; a few minutes is a much more honest ceiling on "how long
    # before a truly abandoned task gets a chance to run again."
    celery_visibility_timeout_seconds: int = 300

    # Auth
    jwt_secret: str = "change-me-in-real-env"
    jwt_algorithm: str = "HS256"
    jwt_access_token_ttl_minutes: int = 15

    # Model provider
    model_provider: Literal["ollama", "vllm", "mock"] = "ollama"
    model_provider_url: str = "http://host.docker.internal:11434"
    default_model: str = "llama3.2:3b"

    # Rate limiting — ERROR_HANDLING.md §1
    rate_limit_requests: int = 60
    rate_limit_window_seconds: int = 60

    # Timeouts — ERROR_HANDLING.md §2
    queue_wait_timeout_seconds: int = 60
    inference_timeout_seconds: int = 30

    # Retry policy — ERROR_HANDLING.md §3
    retry_max_attempts: int = 3
    retry_backoff_base_seconds: float = 0.5

    # Circuit breaker — ERROR_HANDLING.md §4 (Phase 2)
    circuit_breaker_failure_threshold: int = 5
    circuit_breaker_window_seconds: int = 30
    circuit_breaker_cooldown_seconds: int = 20

    # Backpressure — ERROR_HANDLING.md §5 (Phase 2)
    max_queue_depth: int = 200

    # Message limits — API_SPEC.md
    max_message_length: int = 4000


@lru_cache
def get_settings() -> Settings:
    return Settings()
