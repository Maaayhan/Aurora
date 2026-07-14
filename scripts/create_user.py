"""Dev bootstrap: create a `users` row with a fresh API key and print it.

API_SPEC.md deliberately leaves signup out of scope ("not detailed further").
This fills that gap for local development/testing only — there is no
`POST /users` route; provisioning a user is an operator action, not a public
API surface, in this project's scope.

Usage: uv run python scripts/create_user.py
"""

import asyncio
import secrets

from app.core.auth import hash_api_key
from app.core.db import get_session_factory
from app.core.models import User


async def main() -> None:
    raw_key = f"sk-aurora-{secrets.token_urlsafe(24)}"
    session_factory = get_session_factory()
    async with session_factory() as db:
        user = User(api_key_hash=hash_api_key(raw_key))
        db.add(user)
        await db.commit()
        await db.refresh(user)

    print(f"user_id: {user.id}")
    print(f"api_key: {raw_key}")
    print("(save this key now — only the hash is stored)")


if __name__ == "__main__":
    asyncio.run(main())
