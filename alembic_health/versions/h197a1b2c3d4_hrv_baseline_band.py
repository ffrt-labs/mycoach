"""Garmin's HRV baseline band on daily health snapshots

Revision ID: h197a1b2c3d4
Revises: h186a1b2c3d4
Create Date: 2026-11-14 12:00:00.000000

Spec #191, ticket #197 (evidence: docs/research/health-metrics.md section 3). Garmin's HRV
Status compares the 7-day average to a personal baseline band that it sends with every HRV
response as ``hrvSummary.baseline``:

* ``hrv_baseline_low``       <- ``balancedLow``   (bottom of the "Balanced" band)
* ``hrv_baseline_high``      <- ``balancedUpper`` (top of the "Balanced" band)
* ``hrv_baseline_low_upper`` <- ``lowUpper``      (below this the status is "Low")

All three are nullable: Garmin sends no band for roughly the first three weeks of wear.

The columns are backfilled from the raw Garmin response every snapshot already stores in
``raw_data`` (``{"hrv": <hrv response>, ...}``). The JSON is parsed in Python, not SQL, so the
backfill is the same on SQLite and Postgres; unparsable or band-less rows stay NULL. The
parsing is inlined (not imported from the mapper) so this revision keeps meaning what it
meant when the mapper changes.

The ``health_analytics`` views list their columns explicitly, so they are untouched and the
additive-only rule of #113 holds.
"""

import json
from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa

from alembic import op
from mycoach import db_roles

revision: str = "h197a1b2c3d4"
down_revision: str | Sequence[str] | None = "h186a1b2c3d4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "daily_health_snapshots"
COLUMNS = {
    "hrv_baseline_low": "balancedLow",
    "hrv_baseline_high": "balancedUpper",
    "hrv_baseline_low_upper": "lowUpper",
}


def _is_postgres() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def band_from_raw(raw_data: str | None) -> dict[str, float | None] | None:
    """The three band values in a stored raw payload, or None when it carries no band."""
    if not raw_data:
        return None
    try:
        raw = json.loads(raw_data)
    except (TypeError, ValueError):
        return None
    hrv = raw.get("hrv") if isinstance(raw, dict) else None
    if not isinstance(hrv, dict):
        return None
    summary = hrv.get("hrvSummary") or hrv
    baseline = summary.get("baseline") if isinstance(summary, dict) else None
    if not isinstance(baseline, dict):
        return None
    band = {column: _number(baseline.get(key)) for column, key in COLUMNS.items()}
    return band if any(v is not None for v in band.values()) else None


def upgrade() -> None:
    pg = _is_postgres()
    schema = db_roles.HEALTH_SCHEMA if pg else None

    with op.batch_alter_table(TABLE, schema=schema) as batch_op:
        for column in COLUMNS:
            batch_op.add_column(sa.Column(column, sa.Float(), nullable=True))

    snapshots = sa.table(
        TABLE,
        sa.column("id", sa.Integer()),
        sa.column("raw_data", sa.Text()),
        *(sa.column(c, sa.Float()) for c in COLUMNS),
        schema=schema,
    )
    bind = op.get_bind()
    rows = bind.execute(
        sa.select(snapshots.c.id, snapshots.c.raw_data).where(snapshots.c.raw_data.is_not(None))
    ).all()
    for row in rows:
        band = band_from_raw(row.raw_data)
        if band is not None:
            bind.execute(snapshots.update().where(snapshots.c.id == row.id).values(**band))


def downgrade() -> None:
    schema = db_roles.HEALTH_SCHEMA if _is_postgres() else None
    with op.batch_alter_table(TABLE, schema=schema) as batch_op:
        for column in reversed(list(COLUMNS)):
            batch_op.drop_column(column)
