"""source records, canonical activities and the exercise catalogue mirror

Revision ID: h185a1b2c3d4
Revises: h184a1b2c3d4
Create Date: 2026-11-10 12:00:00.000000

Map #85, ticket #185 (design #109, #110, #113). Restructures the health model:

* ``activities`` becomes ``canonical_activities`` and ``gym_workout_details.activity_id``
  becomes ``canonical_activity_id`` (performed sets attach to the canonical activity).
* ``source_records`` (one shared table, immutable) and ``canonical_activity_sources`` (the
  reconciliation link) are created and backfilled: every existing activity gets one source
  record carrying what its provenance columns held, and one link row.
  ``garmin_activity_id`` and ``raw_data`` are dropped from the canonical row once every value
  has a home in a source record.
* ``exercises`` mirrors the pinned catalogue; ``gym_workout_details.exercise_id`` becomes a
  nullable FK to it.
* Postgres: the three ``health_analytics`` views are replaced with ``CREATE OR REPLACE VIEW``
  (which Postgres only accepts if the existing columns are kept, enforcing #113's
  additive-only rule) and a trigger makes ``source_records`` immutable.

Refuses to run (changing nothing) when a ``data_source = 'merged'`` row exists, since
#110's premise (0 merged rows) would not hold and the provenance of such a row was destroyed
by the old merger, or when a set carries an ``exercise_id`` the catalogue does not know.
"""

import hashlib
import json
from collections.abc import Sequence
from datetime import datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op
from mycoach import db_roles
from mycoach.exercise_catalogue import load_exercise_catalogue

revision: str = "h185a1b2c3d4"
down_revision: str | Sequence[str] | None = "h184a1b2c3d4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

IMMUTABLE_FUNCTION = "health.reject_source_record_change"
SET_COLUMNS = (
    "exercise_id",
    "exercise_title",
    "superset_id",
    "exercise_notes",
    "set_index",
    "set_type",
    "weight_kg",
    "reps",
    "distance_meters",
    "duration_seconds",
    "rpe",
)


def _is_postgres() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def _t(name: str, pg: bool) -> str:
    return f"health.{name}" if pg else name


def _derived_external_id(title: str, start_time: datetime) -> str:
    """The identity a source with no native id is recorded under (matches the importer)."""
    digest = hashlib.sha256(f"{title}|{start_time.isoformat()}".encode())
    return f"derived:{digest.hexdigest()[:32]}"


def _guard(pg: bool) -> None:
    bind = op.get_bind()
    merged = bind.execute(
        sa.text(f"SELECT count(*) FROM {_t('activities', pg)} WHERE data_source = 'merged'")
    ).scalar_one()
    if merged:
        raise RuntimeError(
            f"{merged} activity row(s) have data_source = 'merged'. The old merger destroyed "
            "their gym-vs-Hevy distinction and the Garmin raw payload (#110 assumed there "
            "were none), so their source records cannot be reconstructed. Refusing to "
            "migrate; see docs/migrations/185-cutover.md."
        )
    known = {e.id for e in load_exercise_catalogue()}
    used = {
        r[0]
        for r in bind.execute(
            sa.text(
                f"SELECT DISTINCT exercise_id FROM {_t('gym_workout_details', pg)} "
                "WHERE exercise_id IS NOT NULL"
            )
        )
    }
    unknown = sorted(used - known)
    if unknown:
        raise RuntimeError(
            f"gym_workout_details uses exercise_id value(s) the catalogue does not know: "
            f"{', '.join(unknown[:10])}. They cannot become a foreign key to exercises; "
            "refusing to migrate (docs/migrations/185-cutover.md)."
        )


def _json_type(pg: bool) -> sa.types.TypeEngine[Any]:
    return postgresql.JSONB() if pg else sa.JSON()


def _create_new_tables(pg: bool) -> None:
    schema = db_roles.HEALTH_SCHEMA if pg else None
    ca = f"{db_roles.HEALTH_SCHEMA}.canonical_activities" if pg else "canonical_activities"
    op.create_table(
        "exercises",
        sa.Column("id", sa.String(200), primary_key=True),
        sa.Column("title", sa.String(200), nullable=False),
        schema=schema,
    )
    exercises = sa.table(
        "exercises", sa.column("id", sa.String), sa.column("title", sa.String), schema=schema
    )
    op.bulk_insert(exercises, [{"id": e.id, "title": e.name} for e in load_exercise_catalogue()])

    op.create_table(
        "source_records",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("athlete_id", sa.Integer(), nullable=False),
        sa.Column("source", sa.String(20), nullable=False),
        sa.Column("external_id", sa.String(100), nullable=False),
        sa.Column("sport", sa.String(50), nullable=False),
        sa.Column("start_time", sa.DateTime(), nullable=False),
        sa.Column("end_time", sa.DateTime(), nullable=True),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("raw_payload", _json_type(pg), nullable=False),
        sa.Column("received_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint(
            "athlete_id", "source", "external_id", name="uq_source_records_identity"
        ),
        schema=schema,
    )
    op.create_table(
        "canonical_activity_sources",
        sa.Column(
            "canonical_activity_id",
            sa.Integer(),
            sa.ForeignKey(f"{ca}.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "source_record_id",
            sa.Integer(),
            sa.ForeignKey(f"{_t('source_records', pg)}.id"),
            primary_key=True,
            unique=True,
        ),
        schema=schema,
    )


def _backfill(pg: bool) -> None:
    bind = op.get_bind()
    schema = db_roles.HEALTH_SCHEMA if pg else None
    ca = sa.table(
        "canonical_activities",
        sa.column("id", sa.Integer),
        sa.column("athlete_id", sa.Integer),
        sa.column("sport", sa.String),
        sa.column("title", sa.String),
        sa.column("start_time", sa.DateTime),
        sa.column("end_time", sa.DateTime),
        sa.column("data_source", sa.String),
        sa.column("external_id", sa.String),
        sa.column("garmin_activity_id", sa.String),
        sa.column("raw_data", sa.Text),
        sa.column("notes", sa.Text),
        sa.column("created_at", sa.DateTime),
        schema=schema,
    )
    gym = sa.table(
        "gym_workout_details",
        sa.column("id", sa.Integer),
        sa.column("canonical_activity_id", sa.Integer),
        *[sa.column(c) for c in SET_COLUMNS],
        schema=schema,
    )
    sr = sa.Table(
        "source_records",
        sa.MetaData(),
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("athlete_id", sa.Integer),
        sa.Column("source", sa.String),
        sa.Column("external_id", sa.String),
        sa.Column("sport", sa.String),
        sa.Column("start_time", sa.DateTime),
        sa.Column("end_time", sa.DateTime),
        sa.Column("title", sa.String),
        sa.Column("raw_payload", _json_type(pg)),
        sa.Column("received_at", sa.DateTime),
        schema=schema,
    )
    link = sa.table(
        "canonical_activity_sources",
        sa.column("canonical_activity_id", sa.Integer),
        sa.column("source_record_id", sa.Integer),
        schema=schema,
    )

    for a in bind.execute(sa.select(ca).order_by(ca.c.id)).mappings().all():
        if a["data_source"] == "garmin":
            external_id = a["garmin_activity_id"] or f"legacy-activity-{a['id']}"
            try:
                payload = json.loads(a["raw_data"]) if a["raw_data"] else {}
            except ValueError:
                payload = {"raw_data": a["raw_data"]}
            if not isinstance(payload, dict):
                payload = {"raw_data": payload}
        else:
            external_id = a["external_id"] or _derived_external_id(a["title"], a["start_time"])
            sets = [
                {c: row[c] for c in SET_COLUMNS}
                for row in bind.execute(
                    sa.select(gym).where(gym.c.canonical_activity_id == a["id"]).order_by(gym.c.id)
                )
                .mappings()
                .all()
            ]
            payload = {
                "title": a["title"],
                "sport": a["sport"],
                "start_time": a["start_time"].isoformat(),
                "end_time": a["end_time"].isoformat() if a["end_time"] else None,
                "external_id": a["external_id"],
                "notes": a["notes"],
                "sets": sets,
                "backfilled_by": "h185a1b2c3d4",
            }
        record_id = bind.execute(
            sa.insert(sr).values(
                athlete_id=a["athlete_id"],
                source=a["data_source"],
                external_id=external_id,
                sport=a["sport"],
                start_time=a["start_time"],
                end_time=a["end_time"],
                title=a["title"],
                raw_payload=payload,
                received_at=a["created_at"],
            )
        ).inserted_primary_key[0]
        bind.execute(
            sa.insert(link).values(canonical_activity_id=a["id"], source_record_id=record_id)
        )

    counts = bind.execute(
        sa.text(
            f"SELECT (SELECT count(*) FROM {_t('canonical_activities', pg)}), "
            f"(SELECT count(*) FROM {_t('source_records', pg)}), "
            f"(SELECT count(*) FROM {_t('canonical_activity_sources', pg)})"
        )
    ).one()
    if not counts[0] == counts[1] == counts[2]:
        raise RuntimeError(
            f"backfill row counts disagree (activities, source records, links) = {tuple(counts)}"
        )


def _replace_views() -> None:
    """Same columns, same order, new ones appended: Postgres rejects anything else."""
    op.execute(
        """
        CREATE OR REPLACE VIEW health_analytics.activities AS
        SELECT
            a.id, a.athlete_id, a.athlete_id AS user_id, a.sport, a.title,
            a.start_time, a.end_time,
            a.duration_minutes, a.avg_hr, a.max_hr, a.calories, a.hr_zones,
            a.distance_meters, a.avg_speed_mps,
            a.training_effect_aerobic, a.training_effect_anaerobic, a.epoc,
            a.recovery_time_minutes, a.avg_cadence, a.avg_swolf,
            a.moving_duration_seconds, a.fastest_split_100_seconds,
            a.avg_strokes_per_length,
            -- "merged" is no longer a stored value; the view keeps publishing it for an
            -- activity with both a Garmin and a gym source record (additive-only, #113).
            (CASE WHEN EXISTS (
                    SELECT 1 FROM health.canonical_activity_sources cs
                    JOIN health.source_records s ON s.id = cs.source_record_id
                    WHERE cs.canonical_activity_id = a.id AND s.source = 'garmin')
                AND EXISTS (
                    SELECT 1 FROM health.canonical_activity_sources cs
                    JOIN health.source_records s ON s.id = cs.source_record_id
                    WHERE cs.canonical_activity_id = a.id AND s.source <> 'garmin')
                THEN 'merged' ELSE a.data_source END)::varchar(20) AS data_source,
            a.created_at,
            (SELECT array_agg(s.source ORDER BY s.source)
             FROM health.canonical_activity_sources cs
             JOIN health.source_records s ON s.id = cs.source_record_id
             WHERE cs.canonical_activity_id = a.id) AS sources
        FROM health.canonical_activities a
        """
    )
    op.execute(
        """
        CREATE OR REPLACE VIEW health_analytics.gym_sets AS
        SELECT
            d.id, d.canonical_activity_id AS activity_id, a.athlete_id,
            a.athlete_id AS user_id, a.start_time,
            d.exercise_id, d.exercise_title, d.superset_id,
            d.set_index, d.set_type,
            d.weight_kg, d.reps, d.distance_meters, d.duration_seconds, d.rpe,
            d.canonical_activity_id
        FROM health.gym_workout_details d
        JOIN health.canonical_activities a ON a.id = d.canonical_activity_id
        """
    )


def upgrade() -> None:
    pg = _is_postgres()
    schema = db_roles.HEALTH_SCHEMA if pg else None
    _guard(pg)

    if pg:
        op.execute("ALTER TABLE health.activities RENAME TO canonical_activities")
        op.execute(
            "ALTER TABLE health.gym_workout_details "
            "RENAME COLUMN activity_id TO canonical_activity_id"
        )
    else:
        op.rename_table("activities", "canonical_activities")
        with op.batch_alter_table("gym_workout_details") as batch_op:
            batch_op.alter_column("activity_id", new_column_name="canonical_activity_id")

    _create_new_tables(pg)
    _backfill(pg)

    if pg:
        op.execute("ALTER TABLE health.canonical_activities DROP COLUMN garmin_activity_id")
        op.execute("ALTER TABLE health.canonical_activities DROP COLUMN raw_data")
        op.execute(
            "ALTER TABLE health.gym_workout_details ADD CONSTRAINT "
            "gym_workout_details_exercise_id_fkey FOREIGN KEY (exercise_id) "
            "REFERENCES health.exercises (id)"
        )
    else:
        with op.batch_alter_table("canonical_activities") as batch_op:
            batch_op.drop_column("garmin_activity_id")
            batch_op.drop_column("raw_data")
        with op.batch_alter_table("gym_workout_details") as batch_op:
            batch_op.create_foreign_key(
                "gym_workout_details_exercise_id_fkey", "exercises", ["exercise_id"], ["id"]
            )

    if pg:
        _replace_views()
        op.execute(
            f"""
            CREATE FUNCTION {IMMUTABLE_FUNCTION}() RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN
                RAISE EXCEPTION 'source_records are immutable (map #85, design #109): % refused',
                    TG_OP;
            END $$
            """
        )
        op.execute(
            "CREATE TRIGGER source_records_immutable BEFORE UPDATE OR DELETE "
            f"ON health.source_records FOR EACH ROW EXECUTE FUNCTION {IMMUTABLE_FUNCTION}()"
        )
        op.execute(f"ALTER FUNCTION {IMMUTABLE_FUNCTION}() OWNER TO {db_roles.HEALTH.owner}")
        for statement in db_roles.finalize_schema_statements(db_roles.HEALTH):
            op.execute(statement)
    del schema


def downgrade() -> None:
    """Back to the #184 layout. Lossy for a linked Garmin record (the old model could not
    keep it): a canonical activity with both a Garmin and a gym source record returns as
    ``data_source = 'merged'`` with the Garmin id, its raw payload dropped."""
    pg = _is_postgres()
    bind = op.get_bind()
    ca = _t("canonical_activities", pg)

    if pg:
        for view in ("daily_metrics", "activities", "gym_sets"):
            if view != "daily_metrics":
                op.execute(f"DROP VIEW IF EXISTS health_analytics.{view}")
        op.execute("DROP TRIGGER source_records_immutable ON health.source_records")
        op.execute(f"DROP FUNCTION {IMMUTABLE_FUNCTION}()")
        op.execute(
            "ALTER TABLE health.canonical_activities ADD COLUMN garmin_activity_id VARCHAR(100)"
        )
        op.execute("ALTER TABLE health.canonical_activities ADD COLUMN raw_data TEXT")
    else:
        with op.batch_alter_table("canonical_activities") as batch_op:
            batch_op.add_column(sa.Column("garmin_activity_id", sa.String(100), nullable=True))
            batch_op.add_column(sa.Column("raw_data", sa.Text(), nullable=True))

    rows = (
        bind.execute(
            sa.text(
                "SELECT cs.canonical_activity_id AS cid, s.source AS source, s.external_id AS xid, "
                f"s.raw_payload AS payload FROM {_t('canonical_activity_sources', pg)} cs "
                f"JOIN {_t('source_records', pg)} s ON s.id = cs.source_record_id "
                "WHERE s.source = 'garmin'"
            )
        )
        .mappings()
        .all()
    )
    for r in rows:
        payload = r["payload"]
        raw = payload if isinstance(payload, str) else json.dumps(payload)
        gym_sourced = bind.execute(
            sa.text(
                f"SELECT count(*) FROM {_t('canonical_activity_sources', pg)} cs "
                f"JOIN {_t('source_records', pg)} s ON s.id = cs.source_record_id "
                "WHERE cs.canonical_activity_id = :c AND s.source <> 'garmin'"
            ),
            {"c": r["cid"]},
        ).scalar_one()
        if gym_sourced:
            bind.execute(
                sa.text(
                    f"UPDATE {ca} SET garmin_activity_id = :g, data_source = 'merged' WHERE id = :c"
                ),
                {"g": r["xid"], "c": r["cid"]},
            )
        else:
            bind.execute(
                sa.text(f"UPDATE {ca} SET garmin_activity_id = :g, raw_data = :raw WHERE id = :c"),
                {"g": r["xid"], "raw": raw, "c": r["cid"]},
            )
    if pg:
        op.execute(
            "ALTER TABLE health.canonical_activities ADD CONSTRAINT "
            "activities_garmin_activity_id_key UNIQUE (garmin_activity_id)"
        )
        op.execute(
            "ALTER TABLE health.gym_workout_details DROP CONSTRAINT "
            "gym_workout_details_exercise_id_fkey"
        )
    else:
        with op.batch_alter_table("canonical_activities") as batch_op:
            batch_op.create_unique_constraint(
                "uq_activities_garmin_activity_id", ["garmin_activity_id"]
            )
        with op.batch_alter_table("gym_workout_details") as batch_op:
            batch_op.drop_constraint("gym_workout_details_exercise_id_fkey", type_="foreignkey")

    op.drop_table("canonical_activity_sources", schema=db_roles.HEALTH_SCHEMA if pg else None)
    op.drop_table("source_records", schema=db_roles.HEALTH_SCHEMA if pg else None)
    op.drop_table("exercises", schema=db_roles.HEALTH_SCHEMA if pg else None)

    if pg:
        op.execute("ALTER TABLE health.canonical_activities RENAME TO activities")
        op.execute(
            "ALTER TABLE health.gym_workout_details "
            "RENAME COLUMN canonical_activity_id TO activity_id"
        )
        # The #184 views (health_analytics.daily_metrics was never touched here).
        op.execute(
            """
            CREATE VIEW health_analytics.activities AS
            SELECT
                id, athlete_id, athlete_id AS user_id, sport, title, start_time, end_time,
                duration_minutes, avg_hr, max_hr, calories, hr_zones,
                distance_meters, avg_speed_mps,
                training_effect_aerobic, training_effect_anaerobic, epoc,
                recovery_time_minutes, avg_cadence, avg_swolf,
                moving_duration_seconds, fastest_split_100_seconds, avg_strokes_per_length,
                data_source, created_at
            FROM health.activities
            """
        )
        op.execute(
            """
            CREATE VIEW health_analytics.gym_sets AS
            SELECT
                d.id, d.activity_id, a.athlete_id, a.athlete_id AS user_id, a.start_time,
                d.exercise_id, d.exercise_title, d.superset_id,
                d.set_index, d.set_type,
                d.weight_kg, d.reps, d.distance_meters, d.duration_seconds, d.rpe
            FROM health.gym_workout_details d
            JOIN health.activities a ON a.id = d.activity_id
            """
        )
        for view in ("activities", "gym_sets"):
            op.execute(f"ALTER VIEW health_analytics.{view} OWNER TO {db_roles.HEALTH.owner}")
        op.execute(
            "DO $$ BEGIN IF EXISTS (SELECT FROM pg_roles WHERE rolname = 'grafana_role') THEN "
            "GRANT SELECT ON ALL TABLES IN SCHEMA health_analytics TO grafana_role; "
            "END IF; END $$"
        )
    else:
        op.rename_table("canonical_activities", "activities")
        with op.batch_alter_table("gym_workout_details") as batch_op:
            batch_op.alter_column("canonical_activity_id", new_column_name="activity_id")
