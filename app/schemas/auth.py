"""`POST /auth/token` request/response — API_SPEC.md notes this exchange as "not detailed
further — out of scope beyond 'exists and is standard'"; this is that standard implementation:
trade a known API key for a short-lived JWT access token."""

from shared.schemas import CamelModel


class TokenRequest(CamelModel):
    api_key: str


class TokenResponse(CamelModel):
    access_token: str
    token_type: str = "bearer"
    expires_in_seconds: int
