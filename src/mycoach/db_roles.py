"""Postgres roles, schemas and grants for the health/coaching seam (map #85, ticket #108).

One instance, two schemas, least-privilege roles (docs/adr/0001):

* ``health_owner`` / ``coaching_owner``: own their schema and run its migrations (DDL).
* ``health_role`` / ``coaching_role``: DML-only runtime roles. ``coaching_role`` holds no
  grant of any kind on ``health`` (not even ``USAGE``), so a cross-schema query fails with a
  permission error instead of passing review. ``health_role`` mirrors that for ``coaching``.

All roles here are NOLOGIN groups. Giving one a login and password is a secrets step done
by hand (docs/migrations/184-cutover.md), never embedded in a migration.

The statements are plain SQL strings so the migrations, the bootstrap tool and the tests all
read the same definition.
"""

from dataclasses import dataclass

HEALTH_SCHEMA = "health"
COACHING_SCHEMA = "coaching"
ANALYTICS_SCHEMA = "health_analytics"


@dataclass(frozen=True)
class SchemaRoles:
    schema: str
    owner: str
    runtime: str


HEALTH = SchemaRoles(HEALTH_SCHEMA, "health_owner", "health_role")
COACHING = SchemaRoles(COACHING_SCHEMA, "coaching_owner", "coaching_role")

# What the DML-only runtime role may do. DELETE is in: the app deletes plans, insights,
# availability and routines (coaching) and, via the dormant merger and the cleanup tool,
# activities (health). Nothing here ever grants CREATE/ALTER/DROP.
RUNTIME_TABLE_PRIVILEGES = "SELECT, INSERT, UPDATE, DELETE"


def _create_role_if_missing(role: str) -> str:
    return (
        "DO $$ BEGIN "
        f"IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '{role}') THEN "
        f"CREATE ROLE {role} NOLOGIN; "
        "END IF; END $$"
    )


def bootstrap_statements() -> list[str]:
    """Roles and empty schemas, idempotent. Needs a role that may CREATE ROLE (superuser)."""
    statements: list[str] = []
    for roles in (HEALTH, COACHING):
        statements.append(_create_role_if_missing(roles.owner))
        statements.append(_create_role_if_missing(roles.runtime))
    for roles in (HEALTH, COACHING):
        statements += [
            f"CREATE SCHEMA IF NOT EXISTS {roles.schema} AUTHORIZATION {roles.owner}",
            f"ALTER SCHEMA {roles.schema} OWNER TO {roles.owner}",
            f"REVOKE ALL ON SCHEMA {roles.schema} FROM PUBLIC",
            f"GRANT USAGE ON SCHEMA {roles.schema} TO {roles.runtime}",
        ]
    return statements


def finalize_schema_statements(roles: SchemaRoles) -> list[str]:
    """Hand a schema's objects to its owner and (re)grant its runtime role DML.

    Run at the end of every migration that creates or moves objects, so ownership and
    grants never depend on which login happened to run the DDL. Needs a role that may
    ALTER ... OWNER TO the schema owner (superuser, or a member of it).
    """
    schema, owner, runtime = roles.schema, roles.owner, roles.runtime
    return [
        "DO $$ DECLARE r record; BEGIN "
        f"FOR r IN SELECT tablename AS n FROM pg_tables WHERE schemaname = '{schema}' LOOP "
        f"EXECUTE format('ALTER TABLE %I.%I OWNER TO {owner}', '{schema}', r.n); END LOOP; "
        f"FOR r IN SELECT sequencename AS n FROM pg_sequences WHERE schemaname = '{schema}' LOOP "
        f"EXECUTE format('ALTER SEQUENCE %I.%I OWNER TO {owner}', '{schema}', r.n); END LOOP; "
        "END $$",
        f"GRANT USAGE ON SCHEMA {schema} TO {runtime}",
        f"GRANT {RUNTIME_TABLE_PRIVILEGES} ON ALL TABLES IN SCHEMA {schema} TO {runtime}",
        f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA {schema} TO {runtime}",
        # A version table in the schema is the migration tool's, not the runtime's.
        f"DO $$ BEGIN IF to_regclass('{schema}.alembic_version') IS NOT NULL THEN "
        f"REVOKE ALL ON {schema}.alembic_version FROM {runtime}; END IF; END $$",
        f"ALTER DEFAULT PRIVILEGES FOR ROLE {owner} IN SCHEMA {schema} "
        f"GRANT {RUNTIME_TABLE_PRIVILEGES} ON TABLES TO {runtime}",
        f"ALTER DEFAULT PRIVILEGES FOR ROLE {owner} IN SCHEMA {schema} "
        f"GRANT USAGE, SELECT ON SEQUENCES TO {runtime}",
    ]
