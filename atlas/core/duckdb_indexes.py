"""Work around DuckDB's unreliable secondary (ART) indexes.

DuckDB's ART indexes can silently stop matching rows that are plainly present:
an equality lookup such as ``WHERE user_email = ?`` returns nothing while a full
scan of the same table returns the row. Upstream has an open report of the same
shape (duckdb/duckdb#19190). Once a file is affected the damage is durable --
it survives reopening the database, and it is invisible to the application,
which simply sees empty result sets and renders them as "no data".

The blast radius is development only: DuckDB is the local/dev backend and
PostgreSQL is the production one (see ``chat_history.database``). PostgreSQL's
indexes are unaffected and remain necessary there, so this workaround is scoped
to the DuckDB dialect and leaves every other backend untouched.

Dropping the indexes rather than rebuilding them is deliberate. A rebuild
repairs the rows that exist today but leaves the same failure mode armed for
tomorrow; local datasets are small enough that a full scan is cheap, so the
indexes buy nothing and are the only surface the bug needs. Dropping them on
startup also repairs a database that is already affected.
"""

import logging

from sqlalchemy import MetaData
from sqlalchemy.engine import Engine

logger = logging.getLogger(__name__)


def drop_duckdb_secondary_indexes(engine: Engine, metadata: MetaData) -> int:
    """Drop the declared secondary indexes when ``engine`` is backed by DuckDB.

    Only indexes declared on ``metadata`` are touched; the indexes DuckDB
    maintains internally for primary-key and unique constraints are left alone,
    since those enforce correctness rather than accelerate lookups.

    Returns the number of indexes dropped (0 on every non-DuckDB backend).
    """
    if engine.dialect.name != "duckdb":
        return 0

    # ``Index.drop(checkfirst=True)`` cannot be used here: it probes the index
    # via reflection, which duckdb-engine does not implement, so it silently
    # skips every drop while reporting success. Emit the DDL directly instead.
    dropped = 0
    for table in metadata.sorted_tables:
        for index in table.indexes:
            try:
                with engine.begin() as conn:
                    conn.exec_driver_sql(f'DROP INDEX IF EXISTS "{index.name}"')
                dropped += 1
            except Exception:
                # A missing index is the desired end state, so a failure to drop
                # one is worth surfacing but never worth failing startup over.
                logger.warning(
                    "Could not drop DuckDB index %s on %s", index.name, table.name,
                    exc_info=True,
                )

    if dropped:
        logger.info(
            "Dropped %d secondary index(es) on the DuckDB backend; lookups use "
            "full scans instead. See atlas.core.duckdb_indexes for why.",
            dropped,
        )
    return dropped
