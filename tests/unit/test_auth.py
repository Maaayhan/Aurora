import pytest

from app.core.auth import create_access_token, hash_api_key
from app.core.config import Settings
from shared.errors import AuroraError


@pytest.fixture
def settings():
    return Settings(jwt_secret="a-test-secret-that-is-long-enough-1234")


def test_hash_api_key_is_deterministic():
    assert hash_api_key("sk-test") == hash_api_key("sk-test")


def test_hash_api_key_differs_for_different_keys():
    assert hash_api_key("sk-a") != hash_api_key("sk-b")


def test_access_token_roundtrip(settings):
    import jwt as pyjwt

    token = create_access_token("user-123", settings=settings)
    claims = pyjwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
    assert claims["sub"] == "user-123"


async def test_get_current_identity_rejects_missing_credentials():
    from app.core.auth import get_current_identity

    with pytest.raises(AuroraError) as exc_info:
        await get_current_identity(authorization=None, x_api_key=None, db=None, settings=Settings())
    assert exc_info.value.code.value == "UNAUTHENTICATED"


async def test_get_current_identity_rejects_invalid_jwt():
    from app.core.auth import get_current_identity

    with pytest.raises(AuroraError):
        await get_current_identity(
            authorization="Bearer not-a-real-token", x_api_key=None, db=None, settings=Settings()
        )
