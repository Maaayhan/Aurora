async def test_submit_feedback_on_own_message(app_client, fake_dispatch, test_user):
    chat_resp = await app_client.post(
        "/chat", json={"message": "hi"}, headers={"X-API-Key": "sk-test-key"}
    )
    assert chat_resp.status_code == 200

    conv_resp = await app_client.get("/conversations", headers={"X-API-Key": "sk-test-key"})
    conversation_id = conv_resp.json()[0]["id"]
    detail = await app_client.get(
        f"/conversations/{conversation_id}", headers={"X-API-Key": "sk-test-key"}
    )
    assistant_message_id = next(
        m["id"] for m in detail.json()["messages"] if m["role"] == "assistant"
    )

    resp = await app_client.post(
        f"/messages/{assistant_message_id}/feedback",
        json={"rating": 1},
        headers={"X-API-Key": "sk-test-key"},
    )
    assert resp.status_code == 200
    assert resp.json()["rating"] == 1


async def test_feedback_on_unknown_message_is_404(app_client, test_user):
    resp = await app_client.post(
        "/messages/99999999-9999-9999-9999-999999999999/feedback",
        json={"rating": -1},
        headers={"X-API-Key": "sk-test-key"},
    )
    assert resp.status_code == 404
