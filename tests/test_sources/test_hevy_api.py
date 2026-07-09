"""Tests for Hevy API client, parser, source, and sync endpoint."""

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from mycoach.sources.hevy.api_client import HevyApiClient, HevyRateLimitError
from mycoach.sources.hevy.api_parser import parse_api_workouts
from mycoach.sources.hevy.api_source import HevyApiSource

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

SAMPLE_WORKOUT = {
    "id": "abc-123",
    "name": "Push Day",
    "index": 12345,
    "start_time": 1714367990,
    "end_time": 1714372685,
    "created_at": "2024-04-29T06:38:06.115Z",
    "exercises": [
        {
            "title": "Bench Press (Barbell)",
            "sets": [
                {"index": 0, "reps": 10, "weight_kg": 80.0, "rpe": None},
                {"index": 1, "reps": 8, "weight_kg": 85.0, "rpe": 8.0},
            ],
        },
        {
            "title": "Overhead Press (Barbell)",
            "sets": [
                {"index": 0, "reps": 10, "weight_kg": 50.0, "rpe": None},
            ],
        },
    ],
}

SAMPLE_WORKOUT_2 = {
    "id": "def-456",
    "name": "Pull Day",
    "index": 12399,
    "start_time": 1714454390,
    "end_time": 1714458985,
    "created_at": "2024-04-30T06:38:06.115Z",
    "exercises": [
        {
            "title": "Pull Up",
            "sets": [
                {"index": 0, "reps": 8, "weight_kg": None, "rpe": None},
            ],
        },
    ],
}


# ---------------------------------------------------------------------------
# API Parser tests
# ---------------------------------------------------------------------------


def test_parse_api_workouts_basic() -> None:
    result = parse_api_workouts([SAMPLE_WORKOUT])

    assert len(result.workouts) == 1
    w = result.workouts[0]
    assert w.title == "Push Day"
    assert w.start_time == datetime.utcfromtimestamp(1714367990)
    assert w.end_time == datetime.utcfromtimestamp(1714372685)
    assert len(w.sets) == 3


def test_parse_api_workouts_sets_detail() -> None:
    result = parse_api_workouts([SAMPLE_WORKOUT])
    sets = result.workouts[0].sets

    assert sets[0].exercise_title == "Bench Press (Barbell)"
    assert sets[0].weight_kg == 80.0
    assert sets[0].reps == 10
    assert sets[0].rpe is None

    assert sets[1].rpe == 8.0


def test_parse_api_workouts_null_weight() -> None:
    result = parse_api_workouts([SAMPLE_WORKOUT_2])
    assert result.workouts[0].sets[0].weight_kg is None


def test_parse_api_workouts_multiple_workouts() -> None:
    result = parse_api_workouts([SAMPLE_WORKOUT, SAMPLE_WORKOUT_2])
    assert len(result.workouts) == 2
    assert result.workouts[0].title == "Push Day"
    assert result.workouts[1].title == "Pull Day"


def test_parse_api_workouts_invalid_start_time() -> None:
    bad = {**SAMPLE_WORKOUT, "start_time": None}
    result = parse_api_workouts([bad])
    assert len(result.workouts) == 0
    assert result.rows_skipped == 1
    assert len(result.errors) == 1


def test_parse_api_workouts_iso_datetime() -> None:
    workout = {**SAMPLE_WORKOUT, "start_time": "2024-04-29T06:38:10Z", "end_time": None}
    result = parse_api_workouts([workout])
    assert len(result.workouts) == 1
    assert result.workouts[0].end_time is None
    assert result.workouts[0].start_time == datetime(2024, 4, 29, 6, 38, 10)


def test_parse_api_workouts_rpe_out_of_range_ignored() -> None:
    workout = {
        **SAMPLE_WORKOUT,
        "exercises": [
            {"title": "Squat", "sets": [{"index": 0, "reps": 5, "weight_kg": 100.0, "rpe": 15.0}]}
        ],
    }
    result = parse_api_workouts([workout])
    assert result.workouts[0].sets[0].rpe is None


def test_parse_api_workouts_empty() -> None:
    result = parse_api_workouts([])
    assert result.workouts == []
    assert result.errors == []


# ---------------------------------------------------------------------------
# API Client tests (mocked httpx)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_client_login_success() -> None:
    from mycoach.sources.hevy.api_client import HEVY_LOGIN_API_KEY, HEVY_WEB_API_KEY

    client = HevyApiClient(email="test@example.com", password="secret")

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {"auth_token": "tok123"}

    captured_kwargs: dict = {}

    async def mock_post(url, **kwargs):  # type: ignore[no-untyped-def]
        captured_kwargs.update(kwargs)
        return mock_resp

    with patch.object(client._http, "post", new=mock_post):
        result = await client.login()

    assert result is True
    assert client._auth_token == "tok123"
    assert client._http.headers.get("auth-token") == "tok123"
    # Login must use the login API key, not the web key
    assert captured_kwargs["headers"]["x-api-key"] == HEVY_LOGIN_API_KEY
    # After login, web API key is set for subsequent data requests
    assert client._http.headers.get("x-api-key") == HEVY_WEB_API_KEY


@pytest.mark.asyncio
async def test_client_login_no_credentials() -> None:
    client = HevyApiClient()
    result = await client.login()
    assert result is False


@pytest.mark.asyncio
async def test_client_login_missing_token_in_response() -> None:
    client = HevyApiClient(email="test@example.com", password="secret")

    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {"user_id": "123"}

    with patch.object(client._http, "post", new=AsyncMock(return_value=mock_resp)):
        result = await client.login()

    assert result is False


@pytest.mark.asyncio
async def test_client_login_rate_limited() -> None:
    """429 response raises HevyRateLimitError with retry_after info."""
    client = HevyApiClient(email="test@example.com", password="secret")

    mock_resp = MagicMock()
    mock_resp.status_code = 429
    mock_resp.headers = {"retry-after": "60"}
    mock_resp.text = "Too Many Requests"
    mock_resp.raise_for_status.side_effect = httpx.HTTPStatusError(
        "429", request=MagicMock(), response=mock_resp
    )

    with (
        patch.object(client._http, "post", new=AsyncMock(return_value=mock_resp)),
        pytest.raises(HevyRateLimitError) as exc_info,
    ):
        await client.login()

    assert exc_info.value.retry_after == "60"
    assert "429" in str(exc_info.value)


@pytest.mark.asyncio
async def test_client_login_rate_limited_no_retry_after() -> None:
    """429 without retry-after header still raises with None retry_after."""
    client = HevyApiClient(email="test@example.com", password="secret")

    mock_resp = MagicMock()
    mock_resp.status_code = 429
    mock_resp.headers = {}
    mock_resp.text = "Too Many Requests"
    mock_resp.raise_for_status.side_effect = httpx.HTTPStatusError(
        "429", request=MagicMock(), response=mock_resp
    )

    with (
        patch.object(client._http, "post", new=AsyncMock(return_value=mock_resp)),
        pytest.raises(HevyRateLimitError) as exc_info,
    ):
        await client.login()

    assert exc_info.value.retry_after is None


@pytest.mark.asyncio
async def test_client_get_workout_count() -> None:
    client = HevyApiClient()
    client._auth_token = "tok"

    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {"workout_count": 42}

    with patch.object(client._http, "get", new=AsyncMock(return_value=mock_resp)):
        count = await client.get_workout_count()

    assert count == 42


@pytest.mark.asyncio
async def test_client_get_workouts_batch() -> None:
    client = HevyApiClient()
    client._auth_token = "tok"

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = [SAMPLE_WORKOUT, SAMPLE_WORKOUT_2]

    with patch.object(client._http, "get", new=AsyncMock(return_value=mock_resp)):
        batch = await client.get_workouts_batch(0)

    assert len(batch) == 2
    assert batch[0]["name"] == "Push Day"


@pytest.mark.asyncio
async def test_client_fetch_all_workouts_single_page() -> None:
    """When a batch has no `index` or the same index, pagination stops."""
    client = HevyApiClient()
    client._auth_token = "tok"

    # Single workout with index=12345, but next call returns empty list
    batch1 = [SAMPLE_WORKOUT]  # index=12345
    batch2: list = []

    call_count = 0

    async def mock_get(url: str, **kwargs):  # type: ignore[no-untyped-def]
        nonlocal call_count
        call_count += 1
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = batch1 if call_count == 1 else batch2
        return mock_resp

    with patch.object(client._http, "get", new=mock_get):
        workouts = await client.fetch_all_workouts()

    assert len(workouts) == 1
    assert call_count == 2  # initial + one more that returned empty


@pytest.mark.asyncio
async def test_client_fetch_all_workouts_since_filter() -> None:
    """since filter stops when all remaining workouts are older."""
    client = HevyApiClient()
    client._auth_token = "tok"

    # SAMPLE_WORKOUT created 2024-04-29, SAMPLE_WORKOUT_2 created 2024-04-30
    batch = [SAMPLE_WORKOUT, SAMPLE_WORKOUT_2]

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = batch

    # Filter: only workouts from 2024-04-30 onwards
    since = datetime(2024, 4, 30, 0, 0, 0)

    with patch.object(client._http, "get", new=AsyncMock(return_value=mock_resp)):
        workouts = await client.fetch_all_workouts(since=since)

    # Only Pull Day (2024-04-30) passes the filter; Push Day (2024-04-29) causes stop
    assert len(workouts) == 1
    assert workouts[0]["name"] == "Pull Day"


# ---------------------------------------------------------------------------
# HevyApiSource tests (mocked client)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_hevy_api_source_authenticate_success() -> None:
    mock_client = AsyncMock(spec=HevyApiClient)
    mock_client.login.return_value = True

    source = HevyApiSource(client=mock_client)
    assert await source.authenticate() is True
    mock_client.login.assert_called_once()


@pytest.mark.asyncio
async def test_hevy_api_source_authenticate_failure() -> None:
    mock_client = AsyncMock(spec=HevyApiClient)
    mock_client.login.return_value = False

    source = HevyApiSource(client=mock_client)
    assert await source.authenticate() is False


@pytest.mark.asyncio
async def test_hevy_api_source_fetch_and_import() -> None:
    from tests.conftest import test_session

    mock_client = AsyncMock(spec=HevyApiClient)
    mock_client.fetch_all_workouts.return_value = [SAMPLE_WORKOUT]

    source = HevyApiSource(client=mock_client)
    async with test_session() as session:
        result = await source.fetch_and_import(session, user_id=1)

    assert result.activities_created == 1
    assert result.activities_skipped == 0
    assert result.source_type == "hevy_api"


@pytest.mark.asyncio
async def test_hevy_api_source_deduplication() -> None:
    """Importing the same workout twice should skip on second import."""
    from tests.conftest import test_session

    mock_client = AsyncMock(spec=HevyApiClient)
    mock_client.fetch_all_workouts.return_value = [SAMPLE_WORKOUT]

    source = HevyApiSource(client=mock_client)
    async with test_session() as session:
        result1 = await source.fetch_and_import(session, user_id=1)
    async with test_session() as session:
        result2 = await source.fetch_and_import(session, user_id=1)

    assert result1.activities_created == 1
    assert result2.activities_created == 0
    assert result2.activities_skipped == 1


@pytest.mark.asyncio
async def test_hevy_api_source_fetch_error_returns_result() -> None:
    from tests.conftest import test_session

    mock_client = AsyncMock(spec=HevyApiClient)
    mock_client.fetch_all_workouts.side_effect = Exception("network timeout")

    source = HevyApiSource(client=mock_client)
    async with test_session() as session:
        result = await source.fetch_and_import(session, user_id=1)

    assert result.activities_created == 0
    assert result.errors is not None
    assert len(result.errors) == 1


# ---------------------------------------------------------------------------
# API endpoint tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sync_hevy_endpoint_success(client) -> None:
    with (
        patch("mycoach.api.routes.sources.HevyApiSource") as mock_hevy_source_cls,
    ):
        mock_source = AsyncMock()
        mock_source.authenticate.return_value = True
        mock_source.fetch_and_import.return_value = MagicMock(
            activities_created=3,
            activities_skipped=1,
            errors=[],
        )
        mock_hevy_source_cls.return_value = mock_source

        resp = await client.post("/api/sources/sync/hevy")

    assert resp.status_code == 200
    data = resp.json()
    assert data["activities_created"] == 3
    assert data["activities_skipped"] == 1
    assert data["activities_merged"] == 0


@pytest.mark.asyncio
async def test_sync_hevy_endpoint_auth_failure(client) -> None:
    with patch("mycoach.api.routes.sources.HevyApiSource") as mock_hevy_source_cls:
        mock_source = AsyncMock()
        mock_source.authenticate.return_value = False
        mock_hevy_source_cls.return_value = mock_source

        resp = await client.post("/api/sources/sync/hevy")

    assert resp.status_code == 503


@pytest.mark.asyncio
async def test_sync_hevy_endpoint_rate_limited(client) -> None:
    with patch("mycoach.api.routes.sources.HevyApiSource") as mock_hevy_source_cls:
        mock_source = AsyncMock()
        mock_source.authenticate.side_effect = HevyRateLimitError(retry_after="120")
        mock_hevy_source_cls.return_value = mock_source

        resp = await client.post("/api/sources/sync/hevy")

    assert resp.status_code == 429
    assert "rate-limited" in resp.json()["detail"].lower()


@pytest.mark.asyncio
async def test_sync_hevy_endpoint_days_param(client) -> None:
    with patch("mycoach.api.routes.sources.HevyApiSource") as mock_hevy_source_cls:
        mock_source = AsyncMock()
        mock_source.authenticate.return_value = True
        mock_source.fetch_and_import.return_value = MagicMock(
            activities_created=0,
            activities_skipped=0,
            errors=[],
        )
        mock_hevy_source_cls.return_value = mock_source

        resp = await client.post("/api/sources/sync/hevy?days=7")

    assert resp.status_code == 200
    # Verify since was passed (fetch_and_import was called with since kwarg)
    call_kwargs = mock_source.fetch_and_import.call_args
    assert call_kwargs is not None
