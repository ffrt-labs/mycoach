"""The seam holds in code: coaching has no SQL path to health, health never imports coaching.

Map #85, ticket #186. The Postgres permission-error half of this check is in
``tests/test_migrations/test_health_boundary_postgres.py``; this half needs no database.
"""

import ast
import re
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "src" / "mycoach"

# Modules that belong to the health side: they may touch the health schema directly.
HEALTH_SIDE = ("health/", "sources/", "models/", "exercise_catalogue.py", "tools/")

HEALTH_MODEL_MODULES = {
    "mycoach.models.activity",
    "mycoach.models.health",
    "mycoach.models.source_record",
    "mycoach.models.athlete",
    "mycoach.models.data_source",
    "mycoach.models.exercise",
}
HEALTH_MODEL_NAMES = {
    "Activity",
    "GymWorkoutDetail",
    "DailyHealthSnapshot",
    "SourceRecord",
    "CanonicalActivitySource",
    "Athlete",
    "DataSourceConfig",
    "Exercise",
}
HEALTH_TABLES = {
    "canonical_activities",
    "gym_workout_details",
    "daily_health_snapshots",
    "source_records",
    "canonical_activity_sources",
    "athletes",
    "data_source_configs",
    "exercises",
    "health_analytics",
}
# Coaching-owned code and anything that must not be reachable from health.
COACHING_SIDE_PREFIXES = (
    "mycoach.coaching",
    "mycoach.plan_linking",
    "mycoach.scheduler",
    "mycoach.api",
    "mycoach.email",
    "mycoach.observability",
    "mycoach.evals",
    "mycoach.models.plan",
    "mycoach.models.coaching",
    "mycoach.models.user",
    "mycoach.models.prescription",
    "mycoach.models.routine",
    "mycoach.models.availability",
    "mycoach.models.sport_profile",
    "mycoach.models.job_run",
    "mycoach.models.prompt_log",
)


def _py_files() -> list[Path]:
    return sorted(SRC.rglob("*.py"))


def _rel(path: Path) -> str:
    return path.relative_to(SRC).as_posix()


def _is_health_side(path: Path) -> bool:
    rel = _rel(path)
    return any(rel.startswith(p) or rel == p for p in HEALTH_SIDE)


def _imports(tree: ast.AST) -> list[tuple[str, list[str]]]:
    found: list[tuple[str, list[str]]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found += [(a.name, []) for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.append((node.module, [a.name for a in node.names]))
    return found


def test_coaching_code_imports_no_health_model():
    offenders = []
    for path in _py_files():
        if _is_health_side(path):
            continue
        for module, names in _imports(ast.parse(path.read_text())):
            if module in HEALTH_MODEL_MODULES:
                offenders.append(f"{_rel(path)}: {module}")
            if module == "mycoach.models" and HEALTH_MODEL_NAMES.intersection(names):
                offenders.append(f"{_rel(path)}: mycoach.models.{names}")
    assert offenders == []


def test_coaching_code_names_no_health_table_or_schema():
    pattern = re.compile(r"\b(health\.\w+|" + "|".join(sorted(HEALTH_TABLES)) + r")\b")
    offenders = []
    for path in _py_files():
        if _is_health_side(path) or _rel(path) == "db_roles.py":
            continue
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            # string literals only: docstrings are prose, not a SQL path
            if (
                isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and "select" in node.value.lower()
                and pattern.search(node.value)
            ):
                offenders.append(f"{_rel(path)}: {node.value[:60]!r}")
    assert offenders == []


def test_health_side_never_imports_coaching():
    offenders = []
    for path in _py_files():
        rel = _rel(path)
        if not (rel.startswith("health/") or rel.startswith("sources/")):
            continue
        for module, _ in _imports(ast.parse(path.read_text())):
            if module.startswith(COACHING_SIDE_PREFIXES):
                offenders.append(f"{rel}: {module}")
    assert offenders == []


def test_health_models_name_the_health_schema():
    # every model of a health table must live in the health schema, or the runtime role
    # split means nothing
    from mycoach.database import HEALTH_SCHEMA
    from mycoach.models import (
        Activity,
        Athlete,
        CanonicalActivitySource,
        DailyHealthSnapshot,
        DataSourceConfig,
        Exercise,
        GymWorkoutDetail,
        SourceRecord,
    )

    for model in (
        Activity,
        Athlete,
        CanonicalActivitySource,
        DailyHealthSnapshot,
        DataSourceConfig,
        Exercise,
        GymWorkoutDetail,
        SourceRecord,
    ):
        assert model.__table__.schema == HEALTH_SCHEMA
