"""Alembic environment configuration for chat history migrations."""

import sys
from logging.config import fileConfig
from pathlib import Path

from sqlalchemy import engine_from_config, pool

from alembic import context

# Add project root to path so we can import atlas modules
sys.path.insert(0, str(Path(__file__).parent.parent))

from atlas.modules.chat_history.models import Base
from atlas.modules.config.config_manager import (
    pop_prepare_threshold_query,
    resolve_migration_db_url,
)

config = context.config

# Override sqlalchemy.url from environment variables if set. CHAT_HISTORY_DB_URL
# wins; otherwise DB_* components mirror the runtime AppSettings behavior.
# Bare postgresql:// URLs are normalized to postgresql+psycopg:// so migrations
# use the same psycopg (v3) driver as the app regardless of the installed
# SQLAlchemy version (issue #991); resolve_migration_db_url does both, and
# pop_prepare_threshold_query re-types a URL-query prepare_threshold the same
# way the app engine does, handing it to the engine via connect_args.
db_url = resolve_migration_db_url()
if db_url:
    db_url, prepare_args = pop_prepare_threshold_query(db_url)
    config.set_main_option("sqlalchemy.url", db_url)
    config.attributes["connect_args"] = prepare_args

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
        **config.attributes.get("connect_args", {}),
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection, target_metadata=target_metadata
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
