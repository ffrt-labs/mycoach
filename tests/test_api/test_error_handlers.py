"""Tests for global exception handlers."""

from unittest.mock import patch

from fastapi import APIRouter, FastAPI
from httpx import ASGITransport, AsyncClient

from mycoach.api.error_handlers import register_error_handlers


def _make_test_app() -> FastAPI:
    """Create a minimal FastAPI app with error handlers and a broken route."""
    test_app = FastAPI()
    register_error_handlers(test_app)

    router = APIRouter()

    @router.get("/ok")
    async def ok_endpoint() -> dict[str, str]:
        return {"status": "ok"}

    @router.get("/crash")
    async def crash_endpoint() -> None:
        raise RuntimeError("Something broke unexpectedly")

    test_app.include_router(router)
    return test_app


async def test_validation_error_returns_structured_422(client: AsyncClient) -> None:
    """RequestValidationError returns structured JSON with field-level errors."""
    resp = await client.get("/api/health/not-a-date")
    assert resp.status_code == 422
    body = resp.json()
    assert body["error"] == "validation_error"
    assert body["detail"] == "Request validation failed"
    assert isinstance(body["errors"], list)
    assert len(body["errors"]) >= 1
    assert "field" in body["errors"][0]
    assert "message" in body["errors"][0]


async def test_http_exception_returns_structured_json(client: AsyncClient) -> None:
    """HTTPException returns structured JSON with error type and detail."""
    resp = await client.get("/api/coaching/today")
    assert resp.status_code == 404
    body = resp.json()
    assert body["error"] == "http_error"
    assert "detail" in body


async def test_unhandled_exception_returns_500() -> None:
    """Unhandled exceptions return a generic 500 error without leaking details."""
    test_app = _make_test_app()
    transport = ASGITransport(app=test_app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get("/crash")
        assert resp.status_code == 500
        body = resp.json()
        assert body["error"] == "internal_error"
        assert "unexpected" in body["detail"].lower()
        # Should NOT leak the actual error message
        assert "Something broke unexpectedly" not in body["detail"]


async def test_unhandled_exception_is_logged() -> None:
    """Unhandled exceptions are logged with full traceback."""
    test_app = _make_test_app()
    transport = ASGITransport(app=test_app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        with patch("mycoach.api.error_handlers.logger") as mock_logger:
            resp = await ac.get("/crash")
            assert resp.status_code == 500
            mock_logger.exception.assert_called_once()
            call_args = mock_logger.exception.call_args
            assert "GET" in call_args[0][1]
            assert "/crash" in call_args[0][2]


async def test_validation_error_multiple_fields(client: AsyncClient) -> None:
    """Validation errors with multiple bad fields list all of them."""
    resp = await client.post(
        "/api/availability",
        json={"week_start": "not-a-date", "slots": "not-a-list"},
    )
    assert resp.status_code == 422
    body = resp.json()
    assert body["error"] == "validation_error"
    assert len(body["errors"]) >= 1
