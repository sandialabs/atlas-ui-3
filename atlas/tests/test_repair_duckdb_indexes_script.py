"""Regression tests for ``scripts/repair_duckdb_indexes.py``.

These cover the operator-facing failure modes flagged in the PR #816 review:
  * a run that cannot open the file must not leave a backup copy behind
  * the backup keeps its ``.db`` suffix last so the ignore rules still match it
  * ``--check`` and ``--rebuild`` cannot be combined into a silent no-op
  * DDL read back out of the database being repaired is validated before it is
    re-executed
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

duckdb = pytest.importorskip("duckdb")

_SCRIPT_PATH = (
    Path(__file__).resolve().parents[2] / "scripts" / "repair_duckdb_indexes.py"
)
_MODULE_NAME = "_atlas_test_repair_duckdb_indexes"


@pytest.fixture(scope="module")
def repair_mod():
    if not _SCRIPT_PATH.is_file():
        pytest.skip(f"repair script not present at {_SCRIPT_PATH}")
    spec = importlib.util.spec_from_file_location(_MODULE_NAME, _SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[_MODULE_NAME] = module
    spec.loader.exec_module(module)
    try:
        yield module
    finally:
        sys.modules.pop(_MODULE_NAME, None)


@pytest.fixture
def database(tmp_path):
    """A DuckDB file with two secondary indexes and one row."""
    path = tmp_path / "chat_history.db"
    con = duckdb.connect(str(path))
    with con:
        con.execute("create table widgets(id varchar, owner varchar)")
        con.execute("insert into widgets values ('w1', 'someone')")
        con.execute("create index ix_widgets_owner on widgets(owner)")
        con.execute("create index ix_widgets_id on widgets(id)")
    return path


def _index_names(path: Path):
    con = duckdb.connect(str(path), read_only=True)
    with con:
        return sorted(
            row[0] for row in con.execute(
                "select index_name from duckdb_indexes()"
            ).fetchall()
        )


def _backups(path: Path):
    return sorted(p.name for p in path.parent.glob("*prerepair*"))


def test_check_and_rebuild_are_mutually_exclusive(repair_mod, database, monkeypatch):
    """``--check`` returns before any DDL, so the combination must be rejected."""
    monkeypatch.setattr(sys, "argv", ["repair", str(database), "--check", "--rebuild"])

    with pytest.raises(SystemExit) as exc:
        repair_mod.main()

    assert exc.value.code == 2
    assert _index_names(database) == ["ix_widgets_id", "ix_widgets_owner"]


def test_drops_indexes_and_backs_up_with_suffix_last(repair_mod, database, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["repair", str(database)])

    assert repair_mod.main() == 0

    assert _index_names(database) == []
    backups = _backups(database)
    assert len(backups) == 1
    # ``.db`` must stay last: ``data/*.db`` is what keeps a copy of real
    # conversation text out of ``git add -A``.
    assert backups[0].endswith(".db")
    assert backups[0].startswith("chat_history.prerepair-")


def test_no_backup_when_the_database_cannot_be_opened(
    repair_mod, database, monkeypatch
):
    """The likely failure is the app holding the lock; retries must not pile up copies."""
    def _locked(*args, **kwargs):
        raise duckdb.IOException("Could not set lock on file")

    monkeypatch.setattr(repair_mod.duckdb, "connect", _locked)
    monkeypatch.setattr(sys, "argv", ["repair", str(database)])

    assert repair_mod.main() == 1
    assert _backups(database) == []


def test_rebuild_refuses_unrecognized_ddl_before_dropping(
    repair_mod, database, monkeypatch
):
    monkeypatch.setattr(repair_mod, "_validated_create_index", lambda *a: None)
    monkeypatch.setattr(sys, "argv", ["repair", str(database), "--rebuild"])

    assert repair_mod.main() == 1
    assert _index_names(database) == ["ix_widgets_id", "ix_widgets_owner"]


@pytest.mark.parametrize(
    "sql",
    [
        "CREATE INDEX ix_a ON t(a);",
        'create unique index "ix_a" on "t" (a, b)',
        "CREATE INDEX IF NOT EXISTS ix_a ON main.t(a);",
    ],
)
def test_validator_accepts_genuine_create_index(repair_mod, sql):
    assert repair_mod._validated_create_index(sql, "ix_a", "t") is not None


@pytest.mark.parametrize(
    "sql",
    [
        "DROP TABLE t;",
        "CREATE INDEX ix_a ON t(a); DELETE FROM t;",
        "CREATE INDEX ix_other ON t(a);",   # index name does not match the row
        "CREATE INDEX ix_a ON other(a);",   # table name does not match the row
        "",
        None,
    ],
)
def test_validator_rejects_anything_else(repair_mod, sql):
    assert repair_mod._validated_create_index(sql, "ix_a", "t") is None
