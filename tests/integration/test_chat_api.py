"""End-to-end (within a single process) coverage of API_SPEC.md's `POST /chat` contract."""


async def test_unauthenticated_request_is_rejected(app_client, fake_dispatch):
    resp = await app_client.post("/chat", json={"message": "hi"})
    assert resp.status_code == 401
    assert resp.json()["code"] == "UNAUTHENTICATED"


async def test_api_key_auth_streams_a_full_sse_response(app_client, fake_dispatch, test_user):
    resp = await app_client.post(
        "/chat", json={"message": "hi"}, headers={"X-API-Key": "sk-test-key"}
    )
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")
    body = resp.text
    assert "event: token" in body
    assert "event: done" in body
    assert body.index("event: token") < body.index("event: done")


async def test_jwt_auth_via_token_exchange_also_works(app_client, fake_dispatch, test_user):
    token_resp = await app_client.post("/auth/token", json={"apiKey": "sk-test-key"})
    assert token_resp.status_code == 200
    access_token = token_resp.json()["accessToken"]

    resp = await app_client.post(
        "/chat", json={"message": "hi"}, headers={"Authorization": f"Bearer {access_token}"}
    )
    assert resp.status_code == 200
    assert "event: done" in resp.text


async def test_message_over_4000_chars_is_rejected_as_invalid(
    app_client, fake_dispatch, test_user
):
    resp = await app_client.post(
        "/chat", json={"message": "x" * 4001}, headers={"X-API-Key": "sk-test-key"}
    )
    assert resp.status_code == 400
    assert resp.json()["code"] == "INVALID_REQUEST"


async def test_unknown_conversation_id_is_404(app_client, fake_dispatch, test_user):
    resp = await app_client.post(
        "/chat",
        json={"message": "hi", "conversationId": "99999999-9999-9999-9999-999999999999"},
        headers={"X-API-Key": "sk-test-key"},
    )
    assert resp.status_code == 404
    assert resp.json()["code"] == "CONVERSATION_NOT_FOUND"


async def test_rate_limit_returns_429_with_retry_after(
    app_client, fake_dispatch, test_user, monkeypatch
):
    from app.core import rate_limit as rl

    async def always_rejected(*args, **kwargs):
        return rl.RateLimitResult(allowed=False, retry_after_seconds=7)

    monkeypatch.setattr("app.api.chat.check_rate_limit", always_rejected)

    resp = await app_client.post(
        "/chat", json={"message": "hi"}, headers={"X-API-Key": "sk-test-key"}
    )
    assert resp.status_code == 429
    assert resp.json()["code"] == "RATE_LIMITED"
    assert resp.headers["retry-after"] == "7"


async def test_second_message_in_a_conversation_reuses_history(
    app_client, fake_dispatch, test_user
):
    first = await app_client.post(
        "/chat", json={"message": "hi"}, headers={"X-API-Key": "sk-test-key"}
    )
    assert first.status_code == 200

    conv_resp = await app_client.get("/conversations", headers={"X-API-Key": "sk-test-key"})
    assert conv_resp.status_code == 200
    conversations = conv_resp.json()
    assert len(conversations) == 1
    conversation_id = conversations[0]["id"]

    second = await app_client.post(
        "/chat",
        json={"message": "again", "conversationId": conversation_id},
        headers={"X-API-Key": "sk-test-key"},
    )
    assert second.status_code == 200

    detail = await app_client.get(
        f"/conversations/{conversation_id}", headers={"X-API-Key": "sk-test-key"}
    )
    assert detail.status_code == 200
    roles = [m["role"] for m in detail.json()["messages"]]
    assert roles == ["user", "assistant", "user", "assistant"]
