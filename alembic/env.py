"""Alembic environment configuration for chat history migrations."""

import os
import sys
from logging.config import fileConfig
from pathlib import Path

from sqlalchemy import engine_from_config, pool

from alembic import context

# Add project root to path so we can import atlas modules
sys.path.insert(0, str(Path(__file__).parent.parent))

from atlas.modules.chat_history.models import Base
from atlas.modules.config.config_manager import build_db_url_from_parts

config = context.config

# Override sqlalchemy.url from environment variables if set. CHAT_HISTORY_DB_URL
# wins; otherwise DB_* components mirror the runtime AppSettings behavior.
db_url = os.environ.get("CHAT_HISTORY_DB_URL") or build_db_url_from_parts(
    db_driver=os.environ.get("DB_DRIVER", "postgresql"),
    db_host=os.environ.get("DB_HOST"),
    db_port=os.environ.get("DB_PORT"),
    db_name=os.environ.get("DB_NAME"),
    db_user=os.environ.get("DB_USER"),
    db_password=os.environ.get("DB_PASSWORD"),
)
if db_url:
    config.set_main_option("sqlalchemy.url", db_url)

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
