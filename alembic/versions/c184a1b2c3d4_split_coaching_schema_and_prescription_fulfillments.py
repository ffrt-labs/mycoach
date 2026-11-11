"""coaching side of the health/coaching schema seam

Revision ID: c184a1b2c3d4
Revises: a7c41e9d2b56
Create Date: 2026-11-10 00:00:00.000000

Map #85, tickets #108 (design) and #184 (this implementation). The coaching half of the
seam, in the coaching history (the health half is alembic_health/, a separate history
with its own version table):

* Postgres: move every coaching-owned table from ``public`` into the ``coaching`` schema,
  owned by ``coaching_owner``; drop the cross-schema FKs from ``planned_sessions`` and
  ``coaching_insights`` to ``activities`` (the seam allows no cross-schema constraint);
  grant the DML-only ``coaching_role``. Postgres only: SQLite has no schemas.
* Both engines: create ``prescription_fulfillments`` (what the plan called for, per
  performed set) and copy every existing ``gym_workout_details.prescribed_*`` into it, so
  the health history can drop those columns; add ``users.health_athlete_id``.

ORDER MATTERS on the one-time cutover: this runs BEFORE the health history, which refuses
to drop ``prescribed_*`` until the rows are here. The roles and empty schemas must already
exist (``python -m mycoach.tools.db_bootstrap``, docs/migrations/184-cutover.md).

Cutover runs this as the instance's superuser (it reads the still-``public`` health
tables once to copy the prescriptions); later coaching migrations run as ``coaching_owner``.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from mycoach import db_roles

revision: str = "c184a1b2c3d4"
down_revision: str | Sequence[str] | None = "a7c41e9d2b56"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

COACHING_TABLES = (
    "users",
    "workout_routines",
    "routine_days",
    "routine_exercises",
    "weekly_plans",
    "planned_sessions",
    "coaching_insights",
    "mesocycle_configs",
    "prompt_logs",
    "sport_profiles",
    "weekly_availabilities",
    "default_availability",
    "job_runs",
)

_COPY_PRESCRIPTIONS = """
INSERT INTO {ful} (activity_external_id, exercise_title, set_index, exercise_id,
                   prescribed_weight_kg, prescribed_reps, created_at)
SELECT a.external_id, d.exercise_title, d.set_index, d.exercise_id,
       d.prescribed_weight_kg, d.prescribed_reps, CURRENT_TIMESTAMP
FROM gym_workout_details d
JOIN activities a ON a.id = d.activity_id
WHERE a.external_id IS NOT NULL
  AND (d.prescribed_weight_kg IS NOT NULL OR d.prescribed_reps IS NOT NULL)
  AND d.id = (
      SELECT MIN(d2.id) FROM gym_workout_details d2
      JOIN activities a2 ON a2.id = d2.activity_id
      WHERE a2.external_id = a.external_id
        AND d2.exercise_title = d.exercise_title
        AND d2.set_index = d.set_index
        AND (d2.prescribed_weight_kg IS NOT NULL OR d2.prescribed_reps IS NOT NULL)
  )
"""


def _is_postgres() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def _guard_bootstrapped() -> None:
    # Without this the first ALTER fails with a bare "schema does not exist", which reads
    # like a bug rather than a skipped cutover step (it crash-looped production once).
    exists = (
        op.get_bind()
        .execute(
            sa.text("SELECT 1 FROM pg_namespace WHERE nspname = :s"),
            {"s": db_roles.COACHING_SCHEMA},
        )
        .scalar()
    )
    if not exists:
        raise RuntimeError(
            f"schema {db_roles.COACHING_SCHEMA!r} does not exist: this database has not been "
            "through the one-time cutover. Run `python -m mycoach.tools.db_bootstrap` as the "
            "superuser first, then migrate as the superuser (docs/migrations/184-cutover.md)."
        )


def upgrade() -> None:
    pg = _is_postgres()
    schema = db_roles.COACHING_SCHEMA if pg else None

    if pg:
        _guard_bootstrapped()
        for table in COACHING_TABLES:
            op.execute(f"ALTER TABLE public.{table} SET SCHEMA {db_roles.COACHING_SCHEMA}")
        # The seam allows no cross-schema constraint: activity ids are held by value.
        op.execute(
            "ALTER TABLE coaching.planned_sessions "
            "DROP CONSTRAINT IF EXISTS planned_sessions_activity_id_fkey"
        )
        op.execute(
            "ALTER TABLE coaching.coaching_insights "
            "DROP CONSTRAINT IF EXISTS coaching_insights_activity_id_fkey"
        )

    op.create_table(
        "prescription_fulfillments",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("activity_external_id", sa.String(length=100), nullable=False),
        sa.Column("exercise_title", sa.String(length=200), nullable=False),
        sa.Column("set_index", sa.Integer(), nullable=False),
        sa.Column("exercise_id", sa.String(length=200), nullable=True),
        sa.Column("prescribed_weight_kg", sa.Float(), nullable=True),
        sa.Column("prescribed_reps", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint(
            "activity_external_id",
            "exercise_title",
            "set_index",
            name="uq_prescription_fulfillments_activity_exercise_set",
        ),
        schema=schema,
    )
    op.create_index(
        "ix_prescription_fulfillments_activity_external_id",
        "prescription_fulfillments",
        ["activity_external_id"],
        schema=schema,
    )
    ful = "coaching.prescription_fulfillments" if pg else "prescription_fulfillments"
    # Health tables are still in ``public`` (SQLite: the only namespace) when this runs.
    op.execute(_COPY_PRESCRIPTIONS.format(ful=ful))

    # Coaching stores the health side's immutable Athlete ID. Until the health module
    # boundary maps it (#186) the athlete is issued the user's id, so the two coincide.
    with op.batch_alter_table("users", schema=schema) as batch_op:
        batch_op.add_column(sa.Column("health_athlete_id", sa.Integer(), nullable=True))
    op.execute(
        f"UPDATE {'coaching.users' if pg else 'users'} SET health_athlete_id = id "
        "WHERE health_athlete_id IS NULL"
    )

    if pg:
        # The version table stays in ``public`` (the legacy stamp lives there); the owner
        # must be able to write it on later runs.
        op.execute(f"ALTER TABLE public.alembic_version OWNER TO {db_roles.COACHING.owner}")
        for statement in db_roles.finalize_schema_statements(db_roles.COACHING):
            op.execute(statement)


def downgrade() -> None:
    pg = _is_postgres()
    schema = db_roles.COACHING_SCHEMA if pg else None

    with op.batch_alter_table("users", schema=schema) as batch_op:
        batch_op.drop_column("health_athlete_id")
    op.drop_index(
        "ix_prescription_fulfillments_activity_external_id",
        table_name="prescription_fulfillments",
        schema=schema,
    )
    op.drop_table("prescription_fulfillments", schema=schema)

    if pg:
        op.execute(
            "ALTER TABLE coaching.planned_sessions ADD CONSTRAINT "
            "planned_sessions_activity_id_fkey FOREIGN KEY (activity_id) "
            "REFERENCES public.activities (id)"
        )
        op.execute(
            "ALTER TABLE coaching.coaching_insights ADD CONSTRAINT "
            "coaching_insights_activity_id_fkey FOREIGN KEY (activity_id) "
            "REFERENCES public.activities (id)"
        )
        for table in COACHING_TABLES:
            op.execute(f"ALTER TABLE coaching.{table} SET SCHEMA public")
