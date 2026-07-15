"""Circuit breaker state vocabulary, shared between worker/ (which makes breaker decisions,
see worker/reliability.py:CircuitBreaker) and app/ (which only reads state for observability —
GET /metrics's aurora_circuit_breaker_state gauge). Kept here, not in worker/, so app/ never needs
to import anything from worker/ (ARCHITECTURE.md §6's process-boundary discipline) just to read a
Redis hash whose key and field names are already a documented cross-process contract
(DATA_MODEL.md §2).
"""

from enum import StrEnum

CIRCUIT_KEY = "circuit:inference"


class CircuitState(StrEnum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"
