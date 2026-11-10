from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession


@dataclass
class ImportResult:
    """Result of a data source import/sync operation."""

    source_type: str
    activities_created: int = 0
    activities_skipped: int = 0
    health_snapshots_created: int = 0
    health_snapshots_updated: int = 0
    empty_health_days: list[date] = field(default_factory=list)
    errors: list[str] | None = None
    events: list[Any] = field(default_factory=list)
    """Observed-health events the import raised, for the call site to dispatch (#112)."""
    external_ids: list[str] = field(default_factory=list)
    """The external id of each imported workout, in input order (created or already known)."""

    @property
    def has_errors(self) -> bool:
        return bool(self.errors)


class DataSource(ABC):
    """Abstract interface for all data source plugins.

    Each data source (Garmin, Hevy CSV, Strava, etc.) implements this interface
    to provide a consistent way to authenticate, fetch, and import data.
    """

    @property
    @abstractmethod
    def source_type(self) -> str:
        """Identifier for this source (e.g. 'garmin', 'hevy_csv')."""
        ...

    @abstractmethod
    async def authenticate(self) -> bool:
        """Verify credentials and establish a connection.

        Returns True if authentication succeeds, False otherwise.
        """
        ...

    @abstractmethod
    async def fetch_and_import(
        self, session: AsyncSession, user_id: int, since: datetime | None = None
    ) -> ImportResult:
        """Fetch data from the source and import it into the database.

        Args:
            session: Active database session.
            user_id: The user to import data for.
            since: Only fetch data after this timestamp. If None, fetch all available.

        Returns:
            ImportResult with counts and any errors.
        """
        ...
