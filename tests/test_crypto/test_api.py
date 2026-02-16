"""Tests for credential management API endpoints."""

from unittest.mock import patch

from httpx import AsyncClient
from sqlalchemy import select

from mycoach.crypto import decrypt_credentials, generate_key
from mycoach.models.data_source import DataSourceConfig
from tests.conftest import test_session

TEST_KEY = generate_key()


def _patch_settings(**overrides):  # type: ignore[no-untyped-def]
    """Patch get_settings to return a settings object with overrides."""
    from mycoach.config import Settings

    defaults = {
        "env": "test",
        "encryption_key": TEST_KEY,
        "db_url": "sqlite+aiosqlite://",
    }
    defaults.update(overrides)
    settings = Settings(**defaults)
    return patch("mycoach.api.routes.credentials.get_settings", return_value=settings)


async def test_store_credentials(client: AsyncClient) -> None:
    with _patch_settings():
        resp = await client.put(
            "/api/credentials/garmin",
            json={"credentials": {"email": "user@test.com", "password": "secret"}},
        )
    assert resp.status_code == 200
    data = resp.json()
    assert data["source_type"] == "garmin"
    assert data["has_credentials"] is True


async def test_store_credentials_updates_existing(client: AsyncClient) -> None:
    with _patch_settings():
        await client.put(
            "/api/credentials/garmin",
            json={"credentials": {"email": "old@test.com"}},
        )
        resp = await client.put(
            "/api/credentials/garmin",
            json={"credentials": {"email": "new@test.com"}},
        )
    assert resp.status_code == 200
    assert resp.json()["has_credentials"] is True


async def test_store_credentials_no_encryption_key(client: AsyncClient) -> None:
    with _patch_settings(encryption_key=""):
        resp = await client.put(
            "/api/credentials/garmin",
            json={"credentials": {"email": "user@test.com"}},
        )
    assert resp.status_code == 503
    assert "Encryption key not configured" in resp.json()["detail"]


async def test_delete_credentials(client: AsyncClient) -> None:
    with _patch_settings():
        await client.put(
            "/api/credentials/garmin",
            json={"credentials": {"email": "user@test.com"}},
        )
        resp = await client.delete("/api/credentials/garmin")
    assert resp.status_code == 200
    assert "removed" in resp.json()["message"].lower()


async def test_delete_credentials_not_found(client: AsyncClient) -> None:
    resp = await client.delete("/api/credentials/nonexistent")
    assert resp.status_code == 404


async def test_credentials_status_has_credentials(client: AsyncClient) -> None:
    with _patch_settings():
        await client.put(
            "/api/credentials/garmin",
            json={"credentials": {"email": "user@test.com"}},
        )
    resp = await client.get("/api/credentials/garmin/status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["source_type"] == "garmin"
    assert data["has_credentials"] is True


async def test_credentials_status_no_credentials(client: AsyncClient) -> None:
    resp = await client.get("/api/credentials/garmin/status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["has_credentials"] is False


async def test_stored_credentials_are_encrypted(client: AsyncClient) -> None:
    """Verify the DB value is actually encrypted and decryptable."""
    with _patch_settings():
        await client.put(
            "/api/credentials/garmin",
            json={"credentials": {"email": "user@test.com", "password": "s3cr3t"}},
        )

    # Read from DB and verify the value is actually encrypted
    async with test_session() as session:
        result = await session.execute(select(DataSourceConfig))
        config = result.scalar_one()
        assert config.credentials_encrypted is not None
        assert "s3cr3t" not in config.credentials_encrypted

        # Can decrypt back
        decrypted = decrypt_credentials(config.credentials_encrypted, TEST_KEY)
        assert decrypted == {"email": "user@test.com", "password": "s3cr3t"}
