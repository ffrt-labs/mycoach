"""Hevy API client — authenticates via Hevy's internal web API and fetches workouts."""

import fcntl
import json
import logging
import os
import time
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
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
HEVY_WEB_API_KEY = "shelobs_hevy_web"
TOKENS_FILENAME = "tokens.json"
TOKENS_LOCK_FILENAME = "tokens.lock"


def save_tokens(
    token_dir: Path,
    access_token: str,
    refresh_token: str,
    expires_at: str | None = None,
) -> None:
    """Atomically persist the Hevy token pair to ``<token_dir>/tokens.json``.

    Hevy's ``/auth/refresh_token`` requires BOTH the current access token (sent as
    ``Authorization: Bearer``) and the rotating refresh token, so the pair is
    stored together. Written to a temp file then ``os.replace``d into place so a
    crash or concurrent redeploy can never leave a truncated file behind. Usable
    without an authenticated client (e.g. the manual re-seed endpoint).
    """
    path = token_dir / TOKENS_FILENAME
    path.parent.mkdir(parents=True, exist_ok=True)
    payload: dict[str, str] = {
        "access_token": access_token.strip(),
        "refresh_token": refresh_token.strip(),
    }
    if expires_at:
        payload["expires_at"] = expires_at
    tmp = path.with_name(f"{TOKENS_FILENAME}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(payload))
    os.replace(tmp, path)


class HevyApiClient:
    """Async HTTP client for Hevy's internal web API (the same API hevy.com uses).

    Auth model (reverse-engineered from the web app):
      - Every request carries ``x-api-key: shelobs_hevy_web`` + ``hevy-platform: web``.
      - The user is authenticated by a short-lived ``access_token`` (~15 min) sent
        as ``Authorization: Bearer <access_token>``.
      - A new access token is minted from ``POST /auth/refresh_token``, which needs
        BOTH the current (may be just-expired) access token as the Bearer header
        AND the rotating ``refresh_token`` in the body. Each call rotates the
        refresh token, so the new pair is persisted.

    Because refreshing needs a still-valid access token and access tokens expire in
    ~15 min, the pair must be refreshed at least that often to stay alive (see the
    scheduler keep-alive job). The pair is persisted to ``token_dir`` so it survives
    restarts. A one-time manual browser capture is only needed on first setup or
    after the chain lapses (e.g. the server was offline for more than ~15 min).
    There is no headless password login: Hevy's ``/login`` requires a Google
    reCAPTCHA token that only a real browser can produce.
    """

    def __init__(
        self,
        email: str = "",
        password: str = "",
        access_token: str = "",
        refresh_token: str = "",
        token_dir: Path | None = None,
    ) -> None:
        self._email = email
        self._password = password
        self._configured_access_token = access_token
        self._configured_refresh_token = refresh_token
        self._token_dir = token_dir
        self._access_token: str | None = None
        self._http = httpx.AsyncClient(
            base_url=HEVY_API_BASE,
            headers={
                "accept": "application/json, text/plain, */*",
                "accept-language": "en-GB,en-US;q=0.9,en;q=0.8",
                "x-api-key": HEVY_WEB_API_KEY,
                "hevy-platform": "web",
                "origin": "https://hevy.com",
                "referer": "https://hevy.com/",
            },
            timeout=30.0,
        )

    # ---- token persistence -------------------------------------------------

    def _tokens_path(self) -> Path | None:
        return self._token_dir / TOKENS_FILENAME if self._token_dir else None

    def _load_tokens(self) -> tuple[str, str]:
        """Return (access_token, refresh_token) from disk, else configured seeds."""
        path = self._tokens_path()
        if path and path.exists():
            try:
                data = json.loads(path.read_text())
                return data.get("access_token", ""), data.get("refresh_token", "")
            except (ValueError, OSError):
                logger.exception("Hevy tokens file unreadable: %s", path)
        return self._configured_access_token, self._configured_refresh_token

    def _save_tokens(self, access_token: str, refresh_token: str, expires_at: str | None) -> None:
        if self._token_dir is None:
            return
        save_tokens(self._token_dir, access_token, refresh_token, expires_at)

    @contextmanager
    def _refresh_lock(self) -> Iterator[None]:
        """Serialize load→refresh→save across processes/threads.

        The refresh token is single-use and rotates on every call, so the daily
        sync, the keep-alive job, and a manual sync must not race to consume it.
        An exclusive ``flock`` makes waiters block, then re-read the freshly
        rotated pair inside the lock. No token_dir (unit tests) → no-op.
        """
        if self._token_dir is None:
            yield
            return
        self._token_dir.mkdir(parents=True, exist_ok=True)
        lock_path = self._token_dir / TOKENS_LOCK_FILENAME
        with open(lock_path, "w") as lock_file:
            fcntl.flock(lock_file, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock_file, fcntl.LOCK_UN)

    # ---- authentication ----------------------------------------------------

    def _apply_access_token(self, access_token: str) -> None:
        self._access_token = access_token
        self._http.headers["authorization"] = f"Bearer {access_token}"

    async def login(self) -> bool:
        """Authenticate for subsequent requests by refreshing the stored token pair.

        Kept as the DataSource entry point; delegates to :meth:`refresh` since the
        refresh flow is the only headless-viable authentication path.
        """
        return await self.refresh()

    async def refresh(self) -> bool:
        """Mint a fresh access token from the stored token pair (rotating the refresh token).

        Returns False (without a request) if the pair is not seeded. Raises
        HevyRateLimitError on 429.
        """
        with self._refresh_lock():
            return await self._refresh_locked()

    async def _refresh_locked(self) -> bool:
        access_token, refresh_token = self._load_tokens()
        if not refresh_token or not access_token:
            logger.error(
                "Hevy refresh skipped: token pair not seeded "
                "(need both access_token and refresh_token)."
            )
            return False

        try:
            resp = await self._http.post(
                "/auth/refresh_token",
                content=json.dumps({"refresh_token": refresh_token}),
                headers={
                    "content-type": "application/json",
                    "authorization": f"Bearer {access_token}",
                    "x-client-time": f"{time.time():.3f}",
                },
            )
            resp.raise_for_status()
            data = resp.json()
            new_access = data.get("access_token")
            new_refresh = data.get("refresh_token")
            if not new_access or not new_refresh:
                logger.error("Hevy refresh response missing tokens: %s", list(data.keys()))
                return False
            self._apply_access_token(new_access)
            self._save_tokens(new_access, new_refresh, data.get("expires_at"))
            logger.info("Hevy token refresh successful (access expires %s)", data.get("expires_at"))
            return True
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 429:
                retry_after = e.response.headers.get("retry-after")
                logger.error(
                    "Hevy refresh rate-limited (429). Retry after: %s.", retry_after or "unknown"
                )
                raise HevyRateLimitError(retry_after=retry_after) from e
            logger.error(
                "Hevy token refresh failed: HTTP %s — body: %r. The token pair is likely "
                "expired or rotated out of sync; re-seed a fresh pair from a browser login.",
                e.response.status_code,
                e.response.text[:300] or "(empty)",
            )
            return False
        except Exception:
            logger.exception("Hevy token refresh failed")
            return False

    # ---- data fetch --------------------------------------------------------

    async def get_workout_count(self) -> int:
        """Return the total number of workouts for the authenticated user."""
        resp = await self._http.get("/workout_count")
        resp.raise_for_status()
        return int(resp.json().get("workout_count", 0))

    async def get_workouts_batch(self, index: int = 0) -> list[dict[str, Any]]:
        """Fetch a batch of workouts starting from the given cursor index.

        Hevy returns ~10 workouts per batch, ordered oldest-first.
        On 401 (access token expired mid-fetch), refreshes once and retries.
        """
        resp = await self._http.get(f"/workouts_batch/{index}")
        if resp.status_code == 401:
            logger.info("Hevy 401 — refreshing access token")
            if not await self.refresh():
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
