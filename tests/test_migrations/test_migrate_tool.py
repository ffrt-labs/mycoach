"""`python -m mycoach.tools.migrate`: both histories, coaching first (ticket #184)."""

import sqlite3
from pathlib import Path

import pytest

from mycoach.tools import migrate


def test_a_fresh_sqlite_database_gets_both_histories_at_head(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = tmp_path / "fresh.db"
    monkeypatch.setenv("MYCOACH_DB_URL", f"sqlite+aiosqlite:///{db}")

    migrate.main([])

    conn = sqlite3.connect(db)
    try:
        coaching = conn.execute("SELECT version_num FROM alembic_version").fetchall()
        health = conn.execute("SELECT version_num FROM alembic_version_health").fetchall()
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    finally:
        conn.close()
    assert coaching == [("c184a1b2c3d4",)]
    assert health == [("h185a1b2c3d4",)]
    assert {
        "athletes",
        "prescription_fulfillments",
        "source_records",
        "canonical_activities",
        "canonical_activity_sources",
        "exercises",
    } <= tables


def test_running_it_twice_is_a_no_op(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MYCOACH_DB_URL", f"sqlite+aiosqlite:///{tmp_path / 'twice.db'}")
    migrate.main([])
    migrate.main([])
