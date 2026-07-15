"""Unit tests for worker/reliability.py:CircuitBreaker — ERROR_HANDLING.md §4's closed -> open ->
half-open -> closed/open lifecycle."""

import time

import fakeredis

from shared.circuit import CircuitState
from worker.reliability import CircuitBreaker


def _breaker(**overrides) -> CircuitBreaker:
    redis_client = fakeredis.FakeRedis(decode_responses=True)
    defaults = {"failure_threshold": 3, "window_seconds": 30, "cooldown_seconds": 1}
    defaults.update(overrides)
    return CircuitBreaker(redis_client, **defaults)


def test_starts_closed_and_allows_requests():
    breaker = _breaker()
    decision = breaker.allow_request()
    assert decision.allowed is True
    assert decision.state == CircuitState.CLOSED


def test_opens_after_failure_threshold_within_window():
    breaker = _breaker(failure_threshold=3)
    breaker.record_failure()
    breaker.record_failure()
    assert breaker.allow_request().state == CircuitState.CLOSED  # not yet at threshold
    breaker.record_failure()
    decision = breaker.allow_request()
    assert decision.allowed is False
    assert decision.state == CircuitState.OPEN


def test_stays_open_during_cooldown():
    breaker = _breaker(failure_threshold=1, cooldown_seconds=10)
    breaker.record_failure()
    decision = breaker.allow_request()
    assert decision.allowed is False
    assert decision.state == CircuitState.OPEN


def test_transitions_to_half_open_after_cooldown_elapses():
    breaker = _breaker(failure_threshold=1, cooldown_seconds=0.1)
    breaker.record_failure()
    time.sleep(0.15)
    decision = breaker.allow_request()
    assert decision.allowed is True
    assert decision.state == CircuitState.HALF_OPEN


def test_half_open_probe_success_closes_circuit_and_resets_count():
    breaker = _breaker(failure_threshold=1, cooldown_seconds=0.1)
    breaker.record_failure()
    time.sleep(0.15)
    breaker.allow_request()  # transitions to half-open
    breaker.record_success()

    decision = breaker.allow_request()
    assert decision.allowed is True
    assert decision.state == CircuitState.CLOSED


def test_half_open_probe_failure_reopens_circuit():
    breaker = _breaker(failure_threshold=1, cooldown_seconds=0.1)
    breaker.record_failure()
    time.sleep(0.15)
    breaker.allow_request()  # transitions to half-open
    breaker.record_failure()  # probe fails

    decision = breaker.allow_request()
    assert decision.allowed is False
    assert decision.state == CircuitState.OPEN


def test_failures_outside_the_sliding_window_do_not_accumulate():
    breaker = _breaker(failure_threshold=2, window_seconds=0.1)
    breaker.record_failure()
    time.sleep(0.15)  # first failure ages out of the window
    breaker.record_failure()
    # Only 1 failure inside the current window -> still closed, not 2.
    assert breaker.allow_request().state == CircuitState.CLOSED
