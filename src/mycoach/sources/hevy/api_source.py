"""HevyApiSource — fetches workouts from Hevy's internal API and imports into DB."""

import logging
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from mycoach.config import get_settings
from mycoach.sources.base import DataSource, ImportResult
from mycoach.sources.hevy.api_client import HevyApiClient
from mycoach.sources.hevy.api_parser import parse_api_workouts
from mycoach.sources.hevy.mappers import import_hevy_workouts

logger = logging.getLogger(__name__)


class HevyApiSource(DataSource):
    """Fetches gym workouts from Hevy's internal web API.

    Follows the same DataSource interface as GarminSource.
    Falls back gracefully if credentials are not configured.
    """

    def __init__(self, client: HevyApiClient | None = None) -> None:
        settings = get_settings()
        self._client = client or HevyApiClient(
            email=settings.hevy_email,
            password=settings.hevy_password,
        )

    @property
    def source_type(self) -> str:
        return "hevy_api"

    async def authenticate(self) -> bool:
        """Authenticate with Hevy. Raises HevyRateLimitError on 429."""
        return await self._client.login()

    async def fetch_and_import(
        self, session: AsyncSession, user_id: int, since: datetime | None = None
    ) -> ImportResult:
        """Fetch all workouts from Hevy API and import into DB.

        Deduplication is handled by import_hevy_workouts() — workouts with the
        same (title, start_time) that are already in DB are skipped.

        Args:
            session: Active DB session.
            user_id: User to import for.
            since: Optional UTC lower bound; workouts created before this are skipped.

        Returns:
            ImportResult with created/skipped counts and any errors.
        """
        try:
            workouts_json = await self._client.fetch_all_workouts(since=since)
        except Exception as e:
            logger.exception("Hevy API fetch failed")
            return ImportResult(source_type="hevy_api", errors=[str(e)])

        parse_result = parse_api_workouts(workouts_json)
        import_result = await import_hevy_workouts(session, user_id, parse_result)
        import_result.source_type = "hevy_api"
        return import_result
