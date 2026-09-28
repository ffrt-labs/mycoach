"""add health_analytics schema, views, and grafana_role

Revision ID: 7cf0523de01b
Revises: e7f8a9b0c1d2
Create Date: 2026-09-28 00:00:00.000000

Postgres-only (map #85, ticket #117): publishes a read-only, Grafana-facing
projection of the Observed athlete record (CONTEXT.md's "Analytics views"),
reachable through `grafana_role` instead of direct queries against
`daily_health_snapshots`/`activities`/`gym_workout_details`. No-ops on
SQLite — schemas and roles don't exist there, and the app's own test suite
runs `alembic upgrade head` against a throwaway SQLite file (see
tests/test_migrations), same accommodation `743e264deef9` needed in the
other direction.

The `health`/`coaching` schema split described in
docs/adr/0001-single-postgres-instance-two-schemas.md hasn't shipped yet —
today every table above still lives in `public`, owned by the single
`mycoach` role from docker-compose.yml's POSTGRES_USER. So `mycoach` grants
here in place of the ADR's future `health_owner`; regranting from
`health_owner` once that split lands is expected to be mechanical, same as
the ADR's stated plan for moving each schema to its own instance.

Second ADR gap, same root cause: the ADR also calls for "two independent
Alembic histories, one per schema," which doesn't exist either — there is
exactly one `alembic/versions/` history today, chained through both the
health- and coaching-side migrations (this one revises `e7f8a9b0c1d2`, the
most recent coaching-side migration). Splitting the history is a bigger,
separate change than this migration should make unilaterally; flagging it
here rather than silently working within the single shared history.

Debug/free-text/internal-id columns are left out of every view on purpose:
`raw_data` (raw sync payload), `notes`/`exercise_notes` (free text), and
`external_id`/`garmin_activity_id` (source-dedup keys, not observed facts).

`grafana_role` is created WITH NOLOGIN and no password — turning it into a
usable login is a secrets-management step, done by hand per
homelab-host/SECRETS.md conventions (`ALTER ROLE grafana_role WITH LOGIN
PASSWORD '<secret>'`), not something this migration should embed.
"""
from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = '7cf0523de01b'
down_revision: str | Sequence[str] | None = 'e7f8a9b0c1d2'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return

    op.execute("CREATE SCHEMA health_analytics")

    op.execute(
        """
        CREATE VIEW health_analytics.daily_metrics AS
        SELECT
            id, user_id, snapshot_date,
            resting_hr, max_hr, avg_hr,
            hrv_status, hrv_7day_avg, hrv_status_text,
            sleep_duration_minutes, sleep_score, sleep_deep_minutes,
            sleep_light_minutes, sleep_rem_minutes, sleep_awake_minutes,
            body_battery_high, body_battery_low, body_battery_morning, avg_stress,
            training_readiness, training_load, training_status, vo2_max,
            recovery_time_hours, load_focus,
            steps, respiration_avg, spo2_avg, intensity_minutes,
            data_source, created_at, updated_at
        FROM daily_health_snapshots
        """
    )

    op.execute(
        """
        CREATE VIEW health_analytics.activities AS
        SELECT
            id, user_id, sport, title, start_time, end_time, duration_minutes,
            avg_hr, max_hr, calories, hr_zones,
            distance_meters, avg_speed_mps,
            training_effect_aerobic, training_effect_anaerobic, epoc,
            recovery_time_minutes, avg_cadence, avg_swolf,
            moving_duration_seconds, fastest_split_100_seconds, avg_strokes_per_length,
            data_source, created_at
        FROM activities
        """
    )

    op.execute(
        """
        CREATE VIEW health_analytics.gym_sets AS
        SELECT
            d.id, d.activity_id, a.user_id, a.start_time,
            d.exercise_id, d.exercise_title, d.superset_id,
            d.set_index, d.set_type,
            d.weight_kg, d.reps, d.distance_meters, d.duration_seconds, d.rpe,
            d.prescribed_weight_kg, d.prescribed_reps
        FROM gym_workout_details d
        JOIN activities a ON a.id = d.activity_id
        """
    )

    op.execute("CREATE ROLE grafana_role NOLOGIN")
    op.execute("GRANT USAGE ON SCHEMA health_analytics TO grafana_role")
    op.execute("GRANT SELECT ON ALL TABLES IN SCHEMA health_analytics TO grafana_role")
    op.execute(
        "ALTER DEFAULT PRIVILEGES FOR ROLE mycoach IN SCHEMA health_analytics "
        "GRANT SELECT ON TABLES TO grafana_role"
    )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return

    op.execute(
        "ALTER DEFAULT PRIVILEGES FOR ROLE mycoach IN SCHEMA health_analytics "
        "REVOKE SELECT ON TABLES FROM grafana_role"
    )
    op.execute("DROP SCHEMA health_analytics CASCADE")
    op.execute("DROP ROLE grafana_role")
