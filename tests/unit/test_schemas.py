import pytest
from pydantic import ValidationError

from shared.errors import HTTP_STATUS_BY_CODE, AuroraError, ErrorCode
from shared.schemas import ChatRequest, DoneEventData, ErrorEventData, SSEEvent, TokenEventData


def test_sse_event_token_encodes_camelcase_wire_format():
    event = SSEEvent(event="token", data=TokenEventData(content="hi"))
    assert event.encode() == 'event: token\ndata: {"content":"hi"}\n\n'


def test_sse_event_done_uses_camelcase_field_names():
    event = SSEEvent(
        event="done",
        data=DoneEventData(request_id="r1", prompt_tokens=1, completion_tokens=2, latency_ms=10),
    )
    encoded = event.encode()
    assert '"requestId":"r1"' in encoded
    assert '"promptTokens":1' in encoded
    assert '"completionTokens":2' in encoded
    assert '"latencyMs":10' in encoded


def test_sse_event_error_includes_code_message_retryable():
    event = SSEEvent(
        event="error",
        data=ErrorEventData(code=ErrorCode.INFERENCE_TIMEOUT, message="timed out", retryable=True),
    )
    assert event.encode().startswith("event: error\n")


def test_chat_request_rejects_message_over_4000_chars():
    with pytest.raises(ValidationError):
        ChatRequest(message="x" * 4001)


def test_chat_request_accepts_camelcase_conversation_id():
    req = ChatRequest.model_validate({"conversationId": "abc", "message": "hi"})
    assert req.conversation_id == "abc"


def test_every_error_code_has_an_http_status_mapping():
    for code in ErrorCode:
        assert code in HTTP_STATUS_BY_CODE


def test_aurora_error_defaults_retryable_from_code():
    err = AuroraError(ErrorCode.INFERENCE_TIMEOUT, "boom")
    assert err.retryable is True
    err2 = AuroraError(ErrorCode.INVALID_REQUEST, "boom")
    assert err2.retryable is False
