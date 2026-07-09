"""Hevy API client — authenticates via Hevy's internal web API and fetches workouts."""

import logging
from datetime import datetime
from typing import Any

import httpx

logger = logging.getLogger(__name__)


class HevyRateLimitError(Exception):
    """Raised when Hevy returns 429 Too Many Requests."""

    def __init__(self, retry_after: str | None = None) -> None:
        self.retry_after = retry_after
        msg = "Hevy rate-limited (429)."
        if retry_after:
            msg += f" Retry after: {retry_after}s."
        super().__init__(msg)

HEVY_API_BASE = "https://api.hevyapp.com"
HEVY_LOGIN_API_KEY = "with_great_power"
HEVY_WEB_API_KEY = "shelobs_hevy_web"


class HevyApiClient:
    """Async HTTP client for Hevy's internal web API.

    Fetches workout data using the same API the Hevy web app uses.
    Handles login, cursor-based pagination, and 401 re-authentication.
    """

    def __init__(self, email: str = "", password: str = "") -> None:
        self._email = email
        self._password = password
        self._auth_token: str | None = None
        self._http = httpx.AsyncClient(
            base_url=HEVY_API_BASE,
            headers={
                "accept": "application/json, text/plain, */*",
                "accept-encoding": "gzip, deflate, br",
                "accept-language": "en-GB,en-US;q=0.9,en;q=0.8",
            },
            timeout=30.0,
        )

    async def login(self, email: str | None = None, password: str | None = None) -> bool:
        """Authenticate with Hevy and store the auth token for subsequent requests."""
        email = email or self._email
        password = password or self._password
        if not email or not password:
            logger.error("Hevy login skipped: email or password not configured")
            return False

        try:
            import json as json_mod

            logger.debug("Hevy login request to %s%s", HEVY_API_BASE, "/login")
            resp = await self._http.post(
                "/login",
                content=json_mod.dumps({"emailOrUsername": email, "password": password}),
                headers={
                    "content-type": "application/json",
                    "x-api-key": HEVY_LOGIN_API_KEY,
                    "hevy-platform": "android",
                },
            )
            resp.raise_for_status()
            data = resp.json()
            # API may return token under different key names
            self._auth_token = (
                data.get("auth_token")
                or data.get("token")
                or data.get("access_token")
            )
            if self._auth_token:
                self._http.headers["x-api-key"] = HEVY_WEB_API_KEY
                self._http.headers["auth-token"] = self._auth_token
                logger.info("Hevy authentication successful")
                return True
            logger.error("Hevy login response missing auth token: %s", list(data.keys()))
            return False
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 429:
                retry_after = e.response.headers.get("retry-after")
                logger.error(
                    "Hevy login rate-limited (429). Retry after: %s. "
                    "Too many login attempts — wait before retrying.",
                    retry_after or "unknown",
                )
                raise HevyRateLimitError(retry_after=retry_after) from e
            logger.error(
                "Hevy login HTTP error %s — body: %r, response headers: %s",
                e.response.status_code,
                e.response.text[:500] or "(empty)",
                dict(e.response.headers),
            )
            logger.error(
                "Hevy login request details — url: %s, request headers: %s",
                str(e.request.url),
                dict(e.request.headers),
            )
            return False
        except Exception:
            logger.exception("Hevy login failed")
            return False

    async def get_workout_count(self) -> int:
        """Return the total number of workouts for the authenticated user."""
        resp = await self._http.get("/workout_count")
        resp.raise_for_status()
        return int(resp.json().get("workout_count", 0))

    async def get_workouts_batch(self, index: int = 0) -> list[dict[str, Any]]:
        """Fetch a batch of workouts starting from the given cursor index.

        Hevy returns ~10 workouts per batch, ordered oldest-first.
        On 401, re-authenticates once and retries.
        """
        resp = await self._http.get(f"/workouts_batch/{index}")
        if resp.status_code == 401:
            logger.info("Hevy 401 — re-authenticating")
            if not await self.login():
                resp.raise_for_status()
            resp = await self._http.get(f"/workouts_batch/{index}")
        resp.raise_for_status()
        data = resp.json()
        # API returns a JSON array directly
        return data if isinstance(data, list) else data.get("workouts", [])

    async def fetch_all_workouts(self, since: datetime | None = None) -> list[dict[str, Any]]:
        """Fetch all workouts, stopping early when workouts are older than `since`.

        Workouts are returned oldest-first. When `since` is given, pagination
        continues until a workout's `created_at` is older than `since`, then stops.

        Args:
            since: Optional lower bound (naive UTC). Workouts created before this
                   are skipped and pagination stops.

        Returns:
            List of raw workout dicts from the API, newest within the range last.
        """
        all_workouts: list[dict[str, Any]] = []
        index = 0

        while True:
            batch = await self.get_workouts_batch(index)
            if not batch:
                break

            if since is not None:
                filtered: list[dict[str, Any]] = []
                stop = False
                for workout in batch:
                    created_at_str = workout.get("created_at") or ""
                    try:
                        # Parse ISO 8601 with optional Z suffix
                        created_at = datetime.fromisoformat(
                            created_at_str.replace("Z", "+00:00")
                        ).replace(tzinfo=None)
                        if created_at >= since:
                            filtered.append(workout)
                        else:
                            stop = True
                    except (ValueError, AttributeError):
                        filtered.append(workout)
                all_workouts.extend(filtered)
                if stop:
                    break
            else:
                all_workouts.extend(batch)

            # Cursor for next batch is the `index` field of the last workout
            last_index = batch[-1].get("index")
            if last_index is None or last_index == index:
                break
            index = last_index

        return all_workouts

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        await self._http.aclose()
