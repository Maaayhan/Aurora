"""Typed settings, single source of truth for both app/ and worker/ — DEPLOYMENT.md §3.

Validated at process start (pydantic-settings raises on missing/invalid
required vars), so a misconfigured process refuses to boot rather than
failing later at first use. Every threshold named in ERROR_HANDLING.md is an
env var with a documented default here — no magic numbers in code.
"""

from functools import lru_cache
from typing import Literal, Self

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_JWT_SECRET = "change-me-in-real-env"
MIN_JWT_SECRET_BYTES = 32  # HS256: RFC 7518 §3.2 asks for a key at least as long as the hash output


class Settings(BaseSettings):
    # hide_input_in_errors: a failed validation must not print the rejected
    # secret (or the whole settings object) into logs.
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        hide_input_in_errors=True,
    )

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

    # Auth
    jwt_secret: str = DEFAULT_JWT_SECRET  # production refuses this and anything under 32 bytes
    jwt_algorithm: str = "HS256"
    jwt_access_token_ttl_minutes: int = 15

    # Model provider
    model_provider: Literal["ollama", "vllm", "mock"] = "ollama"
    model_provider_url: str = "http://host.docker.internal:11434"
    default_model: str = "llama3.2:3b"

    # Rate limiting — ERROR_HANDLING.md §1
    # Both must be positive: a window of 0 expires every entry at once (no
    # limiting at all) and a limit of 0 rejects every request.
    rate_limit_requests: int = Field(default=60, gt=0)
    rate_limit_window_seconds: int = Field(default=60, gt=0)

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

    @model_validator(mode="after")
    def _require_a_real_jwt_secret_in_production(self) -> Self:
        if self.environment != "production":
            return self
        if self.jwt_secret.lower().startswith("change-me"):
            raise ValueError("JWT_SECRET is still the placeholder value; set a real secret")
        if len(self.jwt_secret.encode()) < MIN_JWT_SECRET_BYTES:
            raise ValueError(
                f"JWT_SECRET must be at least {MIN_JWT_SECRET_BYTES} bytes in production"
            )
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
