"""health side of the health/coaching schema seam

Revision ID: h184a1b2c3d4
Revises:
Create Date: 2026-11-10 00:00:00.000000

Map #85, tickets #108 (design) and #184 (this implementation). First revision of the
health history, which starts from the pre-seam layout the coaching history (``alembic/``)
built, so it carries no baseline of its own:

* Postgres: move ``activities``, ``gym_workout_details``, ``daily_health_snapshots`` and
  ``data_source_configs`` from ``public`` into the ``health`` schema, owned by
  ``health_owner``; drop their FKs to ``users`` (the seam allows no cross-schema
  constraint); grant the DML-only ``health_role``.
* Both engines: ``health.athletes`` issues the Athlete ID; ``user_id`` becomes
  ``athlete_id`` on the three athlete-keyed tables (athlete id = the old user id, so every
  value survives); ``gym_workout_details.prescribed_weight_kg`` / ``prescribed_reps`` are
  dropped, the prescription being coaching's.
* Postgres: rebuild the ``health_analytics`` views over ``health.*``. ``athlete_id`` is
  added next to the old ``user_id`` column (additive-only versioning, #113). The one
  unavoidable break: ``gym_sets`` loses ``prescribed_weight_kg`` / ``prescribed_reps``,
  which are no longer observed facts.

Refuses to drop ``prescribed_*`` until ``prescription_fulfillments`` (coaching history,
revision c184a1b2c3d4) holds every one of them. Run the coaching history first.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from mycoach import db_roles

revision: str = "h184a1b2c3d4"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

HEALTH_TABLES = (
    "activities",
    "gym_workout_details",
    "daily_health_snapshots",
    "data_source_configs",
)
ATHLETE_TABLES = ("activities", "daily_health_snapshots", "data_source_configs")
ANALYTICS_VIEWS = ("daily_metrics", "activities", "gym_sets")

_UNCOPIED = """
SELECT count(*) FROM {gym} d JOIN {act} a ON a.id = d.activity_id
WHERE (d.prescribed_weight_kg IS NOT NULL OR d.prescribed_reps IS NOT NULL)
  AND (a.external_id IS NULL OR NOT EXISTS (
      SELECT 1 FROM {ful} f
      WHERE f.activity_external_id = a.external_id
        AND f.exercise_title = d.exercise_title AND f.set_index = d.set_index))
"""
_NO_EXTERNAL_ID = """
SELECT count(*) FROM gym_workout_details d JOIN activities a ON a.id = d.activity_id
WHERE (d.prescribed_weight_kg IS NOT NULL OR d.prescribed_reps IS NOT NULL)
  AND a.external_id IS NULL
"""


def _is_postgres() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def _fulfillments_table(pg: bool) -> str:
    return "coaching.prescription_fulfillments" if pg else "prescription_fulfillments"


def _guard_prescriptions_are_safe_to_drop(pg: bool) -> None:
    bind = op.get_bind()
    ful = _fulfillments_table(pg)
    exists = (
        bind.execute(sa.text("SELECT to_regclass(:t)"), {"t": ful}).scalar()
        if pg
        else (sa.inspect(bind).has_table("prescription_fulfillments"))
    )
    if not exists:
        raise RuntimeError(
            "prescription_fulfillments is missing: run the coaching history "
            "(`alembic upgrade head`) before the health history, or the prescriptions "
            "on gym_workout_details would be lost."
        )
    no_key = bind.execute(sa.text(_NO_EXTERNAL_ID)).scalar_one()
    if no_key:
        raise RuntimeError(
            f"{no_key} prescribed set(s) belong to a workout with no external id, so they "
            "cannot be keyed into prescription_fulfillments. Refusing to drop prescribed_*; "
            "decide what to do with those rows first (docs/migrations/184-cutover.md)."
        )
    missing = bind.execute(
        sa.text(_UNCOPIED.format(gym="gym_workout_details", act="activities", ful=ful))
    ).scalar_one()
    if missing:
        raise RuntimeError(
            f"{missing} prescribed set(s) are not in prescription_fulfillments yet; "
            "refusing to drop prescribed_*."
        )


def _create_views(health: str, view_owner: str | None) -> None:
    op.execute(
        f"""
        CREATE VIEW health_analytics.daily_metrics AS
        SELECT
            id, athlete_id, athlete_id AS user_id, snapshot_date,
            resting_hr, max_hr, avg_hr,
            hrv_status, hrv_7day_avg, hrv_status_text,
            sleep_duration_minutes, sleep_score, sleep_deep_minutes,
            sleep_light_minutes, sleep_rem_minutes, sleep_awake_minutes,
            body_battery_high, body_battery_low, body_battery_morning, avg_stress,
            training_readiness, training_load, training_status, vo2_max,
            recovery_time_hours, load_focus,
            steps, respiration_avg, spo2_avg, intensity_minutes,
            data_source, created_at, updated_at
        FROM {health}.daily_health_snapshots
        """
    )
    op.execute(
        f"""
        CREATE VIEW health_analytics.activities AS
        SELECT
            id, athlete_id, athlete_id AS user_id, sport, title, start_time, end_time,
            duration_minutes, avg_hr, max_hr, calories, hr_zones,
            distance_meters, avg_speed_mps,
            training_effect_aerobic, training_effect_anaerobic, epoc,
            recovery_time_minutes, avg_cadence, avg_swolf,
            moving_duration_seconds, fastest_split_100_seconds, avg_strokes_per_length,
            data_source, created_at
        FROM {health}.activities
        """
    )
    op.execute(
        f"""
        CREATE VIEW health_analytics.gym_sets AS
        SELECT
            d.id, d.activity_id, a.athlete_id, a.athlete_id AS user_id, a.start_time,
            d.exercise_id, d.exercise_title, d.superset_id,
            d.set_index, d.set_type,
            d.weight_kg, d.reps, d.distance_meters, d.duration_seconds, d.rpe
        FROM {health}.gym_workout_details d
        JOIN {health}.activities a ON a.id = d.activity_id
        """
    )


def _grant_grafana() -> None:
    op.execute(
        "DO $$ BEGIN IF EXISTS (SELECT FROM pg_roles WHERE rolname = 'grafana_role') THEN "
        "GRANT USAGE ON SCHEMA health_analytics TO grafana_role; "
        "GRANT SELECT ON ALL TABLES IN SCHEMA health_analytics TO grafana_role; "
        f"ALTER DEFAULT PRIVILEGES FOR ROLE {db_roles.HEALTH.owner} IN SCHEMA health_analytics "
        "GRANT SELECT ON TABLES TO grafana_role; END IF; END $$"
    )


def upgrade() -> None:
    pg = _is_postgres()
    schema = db_roles.HEALTH_SCHEMA if pg else None
    _guard_prescriptions_are_safe_to_drop(pg)

    if pg:
        for view in ANALYTICS_VIEWS:
            op.execute(f"DROP VIEW IF EXISTS health_analytics.{view}")
        for table in HEALTH_TABLES:
            op.execute(f"ALTER TABLE public.{table} SET SCHEMA {db_roles.HEALTH_SCHEMA}")
        for table in ATHLETE_TABLES:
            # The seam allows no cross-schema constraint; athlete_id is held by value.
            op.execute(f"ALTER TABLE health.{table} DROP CONSTRAINT IF EXISTS {table}_user_id_fkey")
            op.execute(f"ALTER TABLE health.{table} RENAME COLUMN user_id TO athlete_id")
        op.execute("ALTER TABLE health.gym_workout_details DROP COLUMN prescribed_weight_kg")
        op.execute("ALTER TABLE health.gym_workout_details DROP COLUMN prescribed_reps")
    else:
        for table in ATHLETE_TABLES:
            with op.batch_alter_table(table) as batch_op:
                batch_op.alter_column("user_id", new_column_name="athlete_id")
        with op.batch_alter_table("gym_workout_details") as batch_op:
            batch_op.drop_column("prescribed_weight_kg")
            batch_op.drop_column("prescribed_reps")

    op.create_table(
        "athletes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        schema=schema,
    )
    athletes = "health.athletes" if pg else "athletes"
    union = " UNION ".join(
        f"SELECT athlete_id FROM {'health.' if pg else ''}{t}" for t in ATHLETE_TABLES
    )
    # Every existing athlete keeps the id it had as a user id, so no value is rewritten.
    op.execute(f"INSERT INTO {athletes} (id) SELECT DISTINCT athlete_id FROM ({union}) s")
    if pg:
        op.execute(
            "SELECT setval(pg_get_serial_sequence('health.athletes', 'id'), "
            "COALESCE((SELECT MAX(id) FROM health.athletes), 1), "
            "(SELECT MAX(id) IS NOT NULL FROM health.athletes))"
        )

        _create_views(db_roles.HEALTH_SCHEMA, db_roles.HEALTH.owner)
        op.execute(f"ALTER SCHEMA health_analytics OWNER TO {db_roles.HEALTH.owner}")
        for view in ANALYTICS_VIEWS:
            op.execute(f"ALTER VIEW health_analytics.{view} OWNER TO {db_roles.HEALTH.owner}")
        _grant_grafana()
        for statement in db_roles.finalize_schema_statements(db_roles.HEALTH):
            op.execute(statement)


def downgrade() -> None:
    pg = _is_postgres()
    schema = db_roles.HEALTH_SCHEMA if pg else None
    ful = _fulfillments_table(pg)
    gym = "health.gym_workout_details" if pg else "gym_workout_details"
    act = "health.activities" if pg else "activities"

    if pg:
        for view in ANALYTICS_VIEWS:
            op.execute(f"DROP VIEW IF EXISTS health_analytics.{view}")
        op.execute(
            "ALTER TABLE health.gym_workout_details "
            "ADD COLUMN prescribed_weight_kg DOUBLE PRECISION, ADD COLUMN prescribed_reps INTEGER"
        )
    else:
        with op.batch_alter_table("gym_workout_details") as batch_op:
            batch_op.add_column(sa.Column("prescribed_weight_kg", sa.Float(), nullable=True))
            batch_op.add_column(sa.Column("prescribed_reps", sa.Integer(), nullable=True))

    # Carry the prescriptions back onto the performed sets (coaching still holds them).
    for column in ("prescribed_weight_kg", "prescribed_reps"):
        op.execute(
            f"UPDATE {gym} SET {column} = (SELECT f.{column} FROM {ful} f "
            f"JOIN {act} a ON a.external_id = f.activity_external_id "
            f"WHERE a.id = {gym}.activity_id "
            f"AND f.exercise_title = {gym}.exercise_title AND f.set_index = {gym}.set_index)"
        )

    op.drop_table("athletes", schema=schema)

    if pg:
        for table in ATHLETE_TABLES:
            op.execute(f"ALTER TABLE health.{table} RENAME COLUMN athlete_id TO user_id")
            op.execute(
                f"ALTER TABLE health.{table} ADD CONSTRAINT {table}_user_id_fkey "
                "FOREIGN KEY (user_id) REFERENCES coaching.users (id)"
            )
        for table in HEALTH_TABLES:
            op.execute(f"ALTER TABLE health.{table} SET SCHEMA public")
        # The pre-seam views (7cf0523de01b), over ``public``, prescribed_* included.
        op.execute(
            "CREATE VIEW health_analytics.daily_metrics AS SELECT id, user_id, snapshot_date, "
            "resting_hr, max_hr, avg_hr, hrv_status, hrv_7day_avg, hrv_status_text, "
            "sleep_duration_minutes, sleep_score, sleep_deep_minutes, sleep_light_minutes, "
            "sleep_rem_minutes, sleep_awake_minutes, body_battery_high, body_battery_low, "
            "body_battery_morning, avg_stress, training_readiness, training_load, "
            "training_status, vo2_max, recovery_time_hours, load_focus, steps, "
            "respiration_avg, spo2_avg, intensity_minutes, data_source, created_at, "
            "updated_at FROM daily_health_snapshots"
        )
        op.execute(
            "CREATE VIEW health_analytics.activities AS SELECT id, user_id, sport, title, "
            "start_time, end_time, duration_minutes, avg_hr, max_hr, calories, hr_zones, "
            "distance_meters, avg_speed_mps, training_effect_aerobic, "
            "training_effect_anaerobic, epoc, recovery_time_minutes, avg_cadence, avg_swolf, "
            "moving_duration_seconds, fastest_split_100_seconds, avg_strokes_per_length, "
            "data_source, created_at FROM activities"
        )
        op.execute(
            "CREATE VIEW health_analytics.gym_sets AS SELECT d.id, d.activity_id, a.user_id, "
            "a.start_time, d.exercise_id, d.exercise_title, d.superset_id, d.set_index, "
            "d.set_type, d.weight_kg, d.reps, d.distance_meters, d.duration_seconds, d.rpe, "
            "d.prescribed_weight_kg, d.prescribed_reps FROM gym_workout_details d "
            "JOIN activities a ON a.id = d.activity_id"
        )
        _grant_grafana()
    else:
        for table in ATHLETE_TABLES:
            with op.batch_alter_table(table) as batch_op:
                batch_op.alter_column("athlete_id", new_column_name="user_id")
