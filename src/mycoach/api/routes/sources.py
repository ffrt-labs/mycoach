"""Data source management endpoints — sync triggers and Hevy CSV import."""

from fastapi import APIRouter, Depends, UploadFile
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from mycoach.database import get_db
from mycoach.sources.hevy.csv_parser import parse_hevy_csv
from mycoach.sources.hevy.mappers import import_hevy_workouts

router = APIRouter(prefix="/api/sources", tags=["sources"])

# MVP: single user, id=1
DEFAULT_USER_ID = 1


class HevyImportResponse(BaseModel):
    activities_created: int
    activities_skipped: int
    rows_parsed: int
    rows_skipped: int
    errors: list[str]


@router.post("/import/hevy", response_model=HevyImportResponse)
async def import_hevy_csv(
    file: UploadFile,
    session: AsyncSession = Depends(get_db),
) -> HevyImportResponse:
    """Upload and import a Hevy CSV workout export.

    Parses the CSV, deduplicates against existing workouts, and creates
    Activity + GymWorkoutDetail records.
    """
    content = await file.read()
    csv_text = content.decode("utf-8-sig")  # handle BOM from some exports

    parse_result = parse_hevy_csv(csv_text)
    import_result = await import_hevy_workouts(session, DEFAULT_USER_ID, parse_result)

    return HevyImportResponse(
        activities_created=import_result.activities_created,
        activities_skipped=import_result.activities_skipped,
        rows_parsed=parse_result.rows_parsed,
        rows_skipped=parse_result.rows_skipped,
        errors=import_result.errors or [],
    )
