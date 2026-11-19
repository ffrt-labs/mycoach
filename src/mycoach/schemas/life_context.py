from datetime import date

from pydantic import BaseModel, ConfigDict, Field

from mycoach.models.life_context import IllnessSeverity


class IllnessCreate(BaseModel):
    """The dashboard's "Feeling unwell" check-in."""

    severity: IllnessSeverity
    note: str | None = Field(default=None, max_length=500)


class EpisodeUpdate(BaseModel):
    """Edit an episode while it is open; omitted fields stay as they are."""

    severity: IllnessSeverity | None = None
    note: str | None = Field(default=None, max_length=500)


class EpisodeRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    kind: str
    started_on: date
    ended_on: date | None
    note: str | None
    severity: str | None
