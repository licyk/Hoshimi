"""hoshimi.db: numbered migrations, a schema from the future, and the shared-connection helpers."""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

import pytest

from hoshimi.core import db as db_module
from hoshimi.core.db import DB_FILE_NAME, MEMORY, MIGRATIONS, Database, open_store_database, retire_legacy_file


def test_a_new_database_runs_every_migration_once(tmp_path: Path) -> None:
    path = tmp_path / "nested" / DB_FILE_NAME
    db = Database(path)
    assert db.created and db.schema_version == len(MIGRATIONS)
    tables = {row["name"] for row in db.fetchall("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert {"meta", "calls", "usage_hourly", "usage_daily", "usage_hist_hourly", "usage_hist_daily", "lifetime", "checkins", "probe_results"} <= tables
    assert db.fetchone("PRAGMA journal_mode")[0] == "wal"
    db.set_meta("k", "v")
    db.close()
    db.close()  # a second close is a no-op
    assert db.closed

    again = Database(path)
    assert not again.created and again.schema_version == len(MIGRATIONS)
    assert again.get_meta("k") == "v"


def test_migrations_apply_in_order_from_where_the_file_stopped(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / DB_FILE_NAME
    Database(path).close()
    monkeypatch.setattr(db_module, "MIGRATIONS", [*MIGRATIONS, "CREATE TABLE later (x INTEGER);", "INSERT INTO later VALUES (2);"])
    db = Database(path)
    assert db.schema_version == len(MIGRATIONS) + 2
    assert [row["x"] for row in db.fetchall("SELECT x FROM later")] == [2]


def test_a_newer_schema_is_used_with_a_warning(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / DB_FILE_NAME
    with sqlite3.connect(path) as conn:
        conn.execute(f"PRAGMA user_version = {len(MIGRATIONS) + 5}")
    # Patched rather than captured: the CLI tests may have stopped `hoshimi` from propagating.
    warnings: list[str] = []
    monkeypatch.setattr(db_module.logger, "warning", lambda message, *args: warnings.append(message % args))
    db = Database(path)
    assert db.schema_version == len(MIGRATIONS) + 5
    assert len(warnings) == 1 and "newer Hoshimi" in warnings[0]


def test_a_failed_transaction_rolls_back() -> None:
    db = Database()
    assert db.memory and db.path == MEMORY
    with pytest.raises(RuntimeError), db.transaction() as conn:
        conn.execute("INSERT INTO meta (key, value) VALUES ('a', '1')")
        raise RuntimeError("boom")
    assert db.get_meta("a") is None
    db.set_json("j", {"x": [1, "二"]})
    assert db.get_json("j") == {"x": [1, "二"]}
    db.set_meta("bad", "{not json")
    assert db.get_json("bad") is None and db.get_json("missing") is None


def test_the_connection_is_shared_across_threads() -> None:
    db = Database()
    errors: list[BaseException] = []

    def write(index: int) -> None:
        try:
            for step in range(50):
                with db.transaction() as conn:
                    conn.execute("INSERT INTO meta (key, value) VALUES (?, ?)", (f"{index}-{step}", "x"))
        except BaseException as exc:  # noqa: BLE001 - surfaced by the assertion below
            errors.append(exc)

    threads = [threading.Thread(target=write, args=(index,)) for index in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert errors == []
    assert db.fetchone("SELECT COUNT(*) AS n FROM meta")["n"] == 200


def test_store_databases_and_legacy_files(tmp_path: Path) -> None:
    shared = Database()
    assert open_store_database(tmp_path / "stats.json", shared) == (shared, False)
    beside, owned = open_store_database(tmp_path / "stats.json", None)
    assert owned and beside.path == tmp_path / DB_FILE_NAME
    memory, owned = open_store_database(None, None)
    assert owned and memory.memory

    legacy = tmp_path / "probe.json"
    legacy.write_text("{}", encoding="utf-8")
    assert retire_legacy_file(legacy, "migrated") == tmp_path / "probe.json.migrated"
    assert not legacy.exists()
    assert retire_legacy_file(legacy, "migrated") is None  # nothing left to rename
