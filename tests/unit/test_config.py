"""Startup validation: a misconfigured process must refuse to boot, not run
with a publicly known signing key or with rate limiting silently disabled."""

import pytest
from pydantic import ValidationError

from app.core.config import DEFAULT_JWT_SECRET, Settings

STRONG_SECRET = "x" * 32


@pytest.fixture(autouse=True)
def _isolated_environment(monkeypatch, tmp_path):
    """A developer's .env file or exported variables must not leak into the test."""
    monkeypatch.chdir(tmp_path)  # Settings reads ".env" relative to the working directory
    for name in ("ENVIRONMENT", "JWT_SECRET", "RATE_LIMIT_REQUESTS", "RATE_LIMIT_WINDOW_SECONDS"):
        monkeypatch.delenv(name, raising=False)


def make(**overrides) -> Settings:
    return Settings(**overrides)


def test_production_rejects_the_default_jwt_secret():
    with pytest.raises(ValidationError, match="placeholder"):
        make(environment="production", jwt_secret=DEFAULT_JWT_SECRET)


def test_production_rejects_a_placeholder_even_when_long_enough():
    with pytest.raises(ValidationError, match="placeholder"):
        make(environment="production", jwt_secret="change-me-" + "x" * 40)


def test_production_rejects_a_secret_shorter_than_32_bytes():
    with pytest.raises(ValidationError, match="at least 32 bytes"):
        make(environment="production", jwt_secret="x" * 31)


def test_production_counts_bytes_not_characters():
    # 16 two-byte characters are 16 characters but 32 bytes: accepted.
    assert make(environment="production", jwt_secret="é" * 16).jwt_secret == "é" * 16
    # 15 of them are 30 bytes: rejected.
    with pytest.raises(ValidationError, match="at least 32 bytes"):
        make(environment="production", jwt_secret="é" * 15)


def test_production_accepts_a_secret_of_exactly_32_bytes():
    assert make(environment="production", jwt_secret=STRONG_SECRET).environment == "production"


@pytest.mark.parametrize("environment", ["development", "test"])
def test_the_default_secret_still_works_outside_production(environment):
    assert make(environment=environment).jwt_secret == DEFAULT_JWT_SECRET


def test_a_rejected_secret_is_not_echoed_in_the_error():
    # pydantic shortens a long input to "head...tail" in its message, so the
    # check is on a distinctive tail, not on the whole secret.
    tail = "tail-9f3a7c"
    with pytest.raises(ValidationError) as exc_info:
        make(environment="production", jwt_secret="a-weak-secret-" + tail)
    assert tail not in str(exc_info.value)


@pytest.mark.parametrize("value", [0, -1])
def test_rate_limit_requests_must_be_positive(value):
    with pytest.raises(ValidationError, match="rate_limit_requests"):
        make(rate_limit_requests=value)


@pytest.mark.parametrize("value", [0, -60])
def test_rate_limit_window_must_be_positive(value):
    with pytest.raises(ValidationError, match="rate_limit_window_seconds"):
        make(rate_limit_window_seconds=value)


def test_rate_limit_defaults_are_valid():
    settings = make()
    assert settings.rate_limit_requests > 0
    assert settings.rate_limit_window_seconds > 0


# The tests above build Settings from keyword arguments. These go through the
# path a deployed process actually takes: real environment variables, then a
# bare Settings(), as app/core/config.get_settings() does.


def test_env_production_without_a_secret_refuses_to_start(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "production")
    with pytest.raises(ValidationError, match="placeholder"):
        Settings()


def test_env_production_with_a_short_secret_refuses_to_start(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("JWT_SECRET", "x" * 31)
    with pytest.raises(ValidationError, match="at least 32 bytes"):
        Settings()


def test_env_production_with_a_strong_secret_starts(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("JWT_SECRET", STRONG_SECRET)
    assert Settings().jwt_secret == STRONG_SECRET


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("RATE_LIMIT_REQUESTS", "0"),
        ("RATE_LIMIT_WINDOW_SECONDS", "0"),
        ("RATE_LIMIT_REQUESTS", "-3"),
    ],
)
def test_env_non_positive_rate_limit_refuses_to_start(monkeypatch, name, value):
    monkeypatch.setenv(name, value)
    with pytest.raises(ValidationError, match=name.lower()):
        Settings()
