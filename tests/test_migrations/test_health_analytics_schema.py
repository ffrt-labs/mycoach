"""Tests for the health_analytics schema/views/grafana_role migration (7cf0523de01b)."""

import importlib.util
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from alembic.config import Config

from alembic import command

REPO_ROOT = Path(__file__).resolve().parents[2]
MIGRATION_PATH = (
    REPO_ROOT / "alembic" / "versions" / "7cf0523de01b_add_health_analytics_schema_and_.py"
)
PREVIOUS_REVISION = "e7f8a9b0c1d2"


def _alembic_config(db_path: Path, monkeypatch: pytest.MonkeyPatch) -> Config:
    monkeypatch.setenv("MYCOACH_DB_URL", f"sqlite+aiosqlite:///{db_path}")
    cfg = Config(str(REPO_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(REPO_ROOT / "alembic"))
    return cfg


def _load_migration_module() -> Any:
    spec = importlib.util.spec_from_file_location("health_analytics_migration", MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _FakeDialect:
    def __init__(self, name: str) -> None:
        self.name = name


class _FakeBind:
    def __init__(self, dialect_name: str) -> None:
        self.dialect = _FakeDialect(dialect_name)


def test_upgrade_and_downgrade_are_noop_on_sqlite(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The app's test suite runs `alembic upgrade head` against SQLite; this
    migration must not break that chain even though its content is
    Postgres-only."""
    db_path = tmp_path / "health_analytics.db"
    cfg = _alembic_config(db_path, monkeypatch)

    command.upgrade(cfg, "head")
    command.downgrade(cfg, PREVIOUS_REVISION)


def test_upgrade_creates_schema_views_and_role_on_postgres() -> None:
    module = _load_migration_module()
    executed: list[str] = []

    with patch.object(module.op, "get_bind", return_value=_FakeBind("postgresql")), \
         patch.object(module.op, "execute", side_effect=lambda sql: executed.append(sql)):
        module.upgrade()

    joined = "\n".join(executed)
    assert "CREATE SCHEMA health_analytics" in joined
    assert "CREATE VIEW health_analytics.daily_metrics" in joined
    assert "FROM daily_health_snapshots" in joined
    assert "CREATE VIEW health_analytics.activities" in joined
    assert "FROM activities" in joined
    assert "CREATE VIEW health_analytics.gym_sets" in joined
    assert "FROM gym_workout_details" in joined
    assert "CREATE ROLE grafana_role NOLOGIN" in joined
    assert "GRANT USAGE ON SCHEMA health_analytics TO grafana_role" in joined
    assert "GRANT SELECT ON ALL TABLES IN SCHEMA health_analytics TO grafana_role" in joined

    # No debug/free-text/internal-id columns leak into the published views.
    for excluded in ("raw_data", "notes", "exercise_notes", "external_id", "garmin_activity_id"):
        assert excluded not in joined


def test_downgrade_drops_role_and_schema_on_postgres() -> None:
    module = _load_migration_module()
    executed: list[str] = []

    with patch.object(module.op, "get_bind", return_value=_FakeBind("postgresql")), \
         patch.object(module.op, "execute", side_effect=lambda sql: executed.append(sql)):
        module.downgrade()

    joined = "\n".join(executed)
    assert "DROP SCHEMA health_analytics CASCADE" in joined
    assert "DROP ROLE grafana_role" in joined
