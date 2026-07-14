"""Single error code enum shared between app/ and worker/ (API_SPEC.md error catalogue).

Imported on both sides of the process boundary so an error code means the same
thing whether it shows up in an SSE `error` event, an HTTP JSON error body, or
a log line.
"""

from enum import StrEnum


class ErrorCode(StrEnum):
    INVALID_REQUEST = "INVALID_REQUEST"
    UNAUTHENTICATED = "UNAUTHENTICATED"
    CONVERSATION_NOT_FOUND = "CONVERSATION_NOT_FOUND"
    RATE_LIMITED = "RATE_LIMITED"
    SERVER_BUSY = "SERVER_BUSY"
    INFERENCE_TIMEOUT = "INFERENCE_TIMEOUT"
    INFERENCE_UNAVAILABLE = "INFERENCE_UNAVAILABLE"
    INFERENCE_ERROR = "INFERENCE_ERROR"
    CANCELLED = "CANCELLED"


# Whether a failure with this code happened before any token reached the client,
# and is therefore safe to retry per ERROR_HANDLING.md §3 ("retries apply only to
# failures that occur before any token has been streamed"). This is worker-internal
# retry eligibility, not a promise to the client — the client never sees a retry,
# only the final outcome.
RETRYABLE_CODES: frozenset[ErrorCode] = frozenset(
    {ErrorCode.INFERENCE_TIMEOUT, ErrorCode.INFERENCE_ERROR}
)

HTTP_STATUS_BY_CODE: dict[ErrorCode, int] = {
    ErrorCode.INVALID_REQUEST: 400,
    ErrorCode.UNAUTHENTICATED: 401,
    ErrorCode.CONVERSATION_NOT_FOUND: 404,
    ErrorCode.RATE_LIMITED: 429,
    ErrorCode.SERVER_BUSY: 503,
    ErrorCode.INFERENCE_TIMEOUT: 504,
    ErrorCode.INFERENCE_UNAVAILABLE: 503,
    ErrorCode.INFERENCE_ERROR: 502,
    ErrorCode.CANCELLED: 499,
}


class AuroraError(Exception):
    """Base for typed application errors — carries the code that API_SPEC.md contracts on."""

    def __init__(
        self,
        code: ErrorCode,
        message: str,
        *,
        retryable: bool | None = None,
        retry_after_seconds: int | None = None,
    ) -> None:
        self.code = code
        self.message = message
        self.retryable = retryable if retryable is not None else code in RETRYABLE_CODES
        # Set for RATE_LIMITED (API_SPEC.md: 429 response carries a `Retry-After` header).
        self.retry_after_seconds = retry_after_seconds
        super().__init__(message)
