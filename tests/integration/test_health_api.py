"""`GET /health` — API_SPEC.md contract: `healthy`/`degraded`/`unhealthy` distinction from
ERROR_HANDLING.md §7."""

from providers.mock_provider import MockProvider


async def test_degraded_when_only_inference_is_unreachable(app_client):
    # app_client fixture wires Postgres->sqlite and Redis->fakeredis (both reachable);
    # MODEL_PROVIDER defaults to ollama, which isn't running in this test environment.
    resp = await app_client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "degraded"
    assert body["checks"]["postgres"]["healthy"] is True
    assert body["checks"]["redis"]["healthy"] is True
    assert body["checks"]["inference"]["healthy"] is False


async def test_healthy_when_every_dependency_is_up(app_client, monkeypatch):
    from app.api import health as health_module

    monkeypatch.setattr(health_module, "_health_provider", lambda: MockProvider(healthy=True))

    resp = await app_client.get("/health")
    body = resp.json()
    assert body["status"] == "healthy"
    assert body["checks"]["inference"]["healthy"] is True
