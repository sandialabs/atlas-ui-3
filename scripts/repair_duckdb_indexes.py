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
import shutil
import sys
import time
from pathlib import Path

try:
    import duckdb
except ImportError:
    sys.exit("duckdb is not installed in this environment")


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
    parser.add_argument(
        "--rebuild", action="store_true",
        help="recreate the indexes after dropping them instead of leaving them off",
    )
    parser.add_argument(
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

    if not args.check and not args.no_backup:
        stamp = time.strftime("%Y%m%d-%H%M%S")
        backup = args.database.with_name(f"{args.database.name}.prerepair-{stamp}")
        shutil.copy2(args.database, backup)
        wal = args.database.with_name(args.database.name + ".wal")
        if wal.exists():
            shutil.copy2(wal, backup.with_name(backup.name + ".wal"))
        print(f"backed up to {backup}")

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

        indexes = con.execute("select index_name, sql from duckdb_indexes()").fetchall()
        if not indexes:
            print("no secondary indexes present -- nothing to repair")
            return 0

        for name, _sql in indexes:
            con.execute(f'DROP INDEX "{name}"')
            print(f"  dropped {name}")

        if args.rebuild:
            for name, sql in indexes:
                con.execute(sql)
                print(f"  rebuilt {name}")

        con.execute("CHECKPOINT")

    print(
        f"{len(indexes)} index(es) "
        f"{'rebuilt' if args.rebuild else 'dropped'}; rows are now visible to lookups"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
