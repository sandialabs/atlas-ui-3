#!/usr/bin/env python3
"""Repair a DuckDB database whose secondary indexes have stopped matching rows.

Symptom: rows are present in the file but invisible to the application. In the
chat history database this shows up as conversations that open with no messages
and custom prompts missing from the prompt list, while the conversation list
itself still looks complete. An equality lookup returns nothing where a full
scan of the same table returns the row -- see ``atlas.core.duckdb_indexes``.

Only the indexes are wrong; no row data is lost. Dropping the indexes restores
correct results immediately.

The application drops these indexes automatically on startup, so an existing
database usually repairs itself. Use this script to repair a file out of band,
to confirm a database is affected, or to rebuild rather than drop.

Usage:
    python scripts/repair_duckdb_indexes.py data/chat_history.db
    python scripts/repair_duckdb_indexes.py data/chat_history.db --rebuild
    python scripts/repair_duckdb_indexes.py data/chat_history.db --check

Stop the application first: DuckDB takes an exclusive lock on the file.
"""

import argparse
import re
import shutil
import sys
import time
from pathlib import Path

try:
    import duckdb
except ImportError:
    sys.exit("duckdb is not installed in this environment")


# ``sql`` in ``duckdb_indexes()`` comes from the database file being repaired,
# not from this codebase, so it is re-executed only after it is confirmed to be
# a single ``CREATE INDEX`` statement for the index and table named in the same
# row. Anything else is skipped rather than run.
_CREATE_INDEX_RE = re.compile(
    r"""^\s*CREATE\s+(?:UNIQUE\s+)?INDEX\s+(?:IF\s+NOT\s+EXISTS\s+)?
        "?(?P<index>[^"\s(.]+)"?\s+ON\s+
        (?:"?[^"\s(.]+"?\.)?"?(?P<table>[^"\s(.]+)"?\s*\(""",
    re.IGNORECASE | re.VERBOSE,
)


def _validated_create_index(sql: str, index_name: str, table_name: str) -> str | None:
    """Return ``sql`` if it is a CREATE INDEX for this index and table, else None."""
    if not isinstance(sql, str):
        return None
    statement = sql.strip().rstrip(";").strip()
    if ";" in statement:  # more than one statement smuggled into the row
        return None
    match = _CREATE_INDEX_RE.match(statement)
    if not match:
        return None
    if match.group("index") != index_name or match.group("table") != table_name:
        return None
    return statement


def _backup(database: Path) -> Path:
    """Copy the database (and its WAL) beside itself, keeping the suffix last.

    The suffix must stay last so the copy keeps matching the ``data/*.db`` and
    ``data/*.wal`` ignore rules -- a backup of the chat history holds user
    emails and conversation text and must never be stageable by ``git add -A``.
    """
    stamp = time.strftime("%Y%m%d-%H%M%S")
    backup = database.with_name(f"{database.stem}.prerepair-{stamp}{database.suffix}")
    shutil.copy2(database, backup)
    wal = database.with_name(database.name + ".wal")
    if wal.exists():
        shutil.copy2(wal, backup.with_name(backup.name + ".wal"))
    return backup


def _check(con) -> int:
    """List the secondary indexes present, with the row count of each table."""
    indexes = con.execute(
        "select index_name, table_name from duckdb_indexes()"
    ).fetchall()
    if not indexes:
        print("no secondary indexes present -- nothing to repair")
        return 0

    for index_name, table_name in indexes:
        total = con.execute(f'select count(*) from "{table_name}"').fetchone()[0]
        print(f"  {index_name} on {table_name} ({total} rows)")
    print(f"{len(indexes)} index(es) present; run without --check to drop them")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("database", type=Path, help="path to the DuckDB file")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--rebuild", action="store_true",
        help="recreate the indexes after dropping them instead of leaving them off",
    )
    mode.add_argument(
        "--check", action="store_true",
        help="report the indexes present and exit without modifying the file",
    )
    parser.add_argument(
        "--no-backup", action="store_true", help="skip the pre-repair copy",
    )
    args = parser.parse_args()

    if not args.database.exists():
        print(f"no database at {args.database}", file=sys.stderr)
        return 1

    try:
        con = duckdb.connect(str(args.database), read_only=args.check)
    except duckdb.IOException as exc:
        print(
            f"cannot open the database -- stop the application first:\n  {exc}",
            file=sys.stderr,
        )
        return 1

    with con:
        if args.check:
            return _check(con)

        indexes = con.execute(
            "select index_name, table_name, sql from duckdb_indexes()"
        ).fetchall()
        if not indexes:
            print("no secondary indexes present -- nothing to repair")
            return 0

        # The backup is taken only once the file is open: the likely failure is
        # the application still holding the exclusive lock, and a copy taken
        # from underneath a live writer is both useless and repeated on every
        # retry.
        if not args.no_backup:
            print(f"backed up to {_backup(args.database)}")

        rebuild_sql = []
        if args.rebuild:
            for name, table, sql in indexes:
                validated = _validated_create_index(sql, name, table)
                if validated is None:
                    print(
                        f"  refusing to rebuild {name}: stored DDL is not a "
                        f"CREATE INDEX for {name} on {table}",
                        file=sys.stderr,
                    )
                    return 1
                rebuild_sql.append((name, validated))

        for name, _table, _sql in indexes:
            con.execute(f'DROP INDEX "{name}"')
            print(f"  dropped {name}")

        for name, sql in rebuild_sql:
            con.execute(sql)
            print(f"  rebuilt {name}")

        con.execute("CHECKPOINT")

    if args.rebuild:
        print(
            f"{len(indexes)} index(es) rebuilt; rows are visible to lookups again, "
            "but the rebuilt indexes can fail the same way and the application "
            "drops them again on its next startup"
        )
    else:
        print(f"{len(indexes)} index(es) dropped; rows are now visible to lookups")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
