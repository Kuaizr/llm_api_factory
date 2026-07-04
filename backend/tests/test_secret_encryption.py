import json

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import Settings
from app.db import migrations
from app.db.base import Base
from app.db.models import APIKey, Endpoint
from app.services.secrets import (
    ENCRYPTED_SECRET_PREFIX,
    decrypt_oauth_config,
    decrypt_secret_value,
    encrypt_oauth_config,
    encrypt_secret_value,
)


def test_secret_value_round_trip() -> None:
    settings = Settings(master_auth_token="token")

    encrypted = encrypt_secret_value("sk-secret", settings=settings)

    assert encrypted is not None
    assert encrypted.startswith(ENCRYPTED_SECRET_PREFIX)
    assert "sk-secret" not in encrypted
    assert decrypt_secret_value(encrypted, settings=settings) == "sk-secret"


def test_oauth_config_encrypts_secret_fields_only() -> None:
    settings = Settings(master_auth_token="token")

    encrypted = encrypt_oauth_config(
        {
            "token_url": "https://auth.example.com/oauth/token",
            "client_id": "client",
            "client_secret": "secret",
        },
        settings=settings,
    )

    assert encrypted is not None
    assert encrypted["client_id"] == "client"
    assert encrypted["client_secret"].startswith(ENCRYPTED_SECRET_PREFIX)
    assert decrypt_oauth_config(encrypted, settings=settings) == {
        "token_url": "https://auth.example.com/oauth/token",
        "client_id": "client",
        "client_secret": "secret",
    }


@pytest.mark.asyncio
async def test_schema_update_encrypts_existing_plaintext_secrets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_maker = async_sessionmaker(engine, expire_on_commit=False)
    async with session_maker() as session:
        endpoint = Endpoint(
            name="OAuth",
            base_url="https://api.example.com",
            oauth_config=json.dumps(
                {
                    "token_url": "https://auth.example.com/oauth/token",
                    "client_id": "client",
                    "client_secret": "oauth-secret",
                }
            ),
            is_active=True,
        )
        session.add(endpoint)
        await session.commit()
        await session.refresh(endpoint)
        session.add(APIKey(endpoint_id=endpoint.id, key="sk-plain", is_active=True))
        await session.commit()

    settings = Settings(master_auth_token="token")
    monkeypatch.setattr(migrations, "get_settings", lambda: settings)

    await migrations.apply_schema_updates(engine)

    async with session_maker() as session:
        api_key = (await session.execute(select(APIKey))).scalar_one()
        endpoint = (await session.execute(select(Endpoint))).scalar_one()

    assert api_key.key.startswith(ENCRYPTED_SECRET_PREFIX)
    assert decrypt_secret_value(api_key.key, settings=settings) == "sk-plain"

    stored_oauth = json.loads(endpoint.oauth_config)
    assert stored_oauth["client_secret"].startswith(ENCRYPTED_SECRET_PREFIX)
    assert decrypt_oauth_config(stored_oauth, settings=settings)["client_secret"] == "oauth-secret"

    await engine.dispose()
