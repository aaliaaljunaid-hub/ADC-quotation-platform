"""
Alembic environment. Reads DATABASE_URL the same way app/database.py does
(so migrations always target the same DB the app would connect to) rather
than duplicating a connection string in alembic.ini, and imports every ORM
model so autogenerate can see the full schema via Base.metadata.
"""
import os
import sys
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.database import Base  # noqa: E402
from app import db_models  # noqa: E402,F401  populates Base.metadata

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

config.set_main_option("sqlalchemy.url", os.environ.get("DATABASE_URL", "sqlite:///./quotation.db"))

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url, target_metadata=target_metadata, literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        # SQLite can't ALTER most column properties in place; batch mode
        # works around that by rebuilding the table. Harmless on Postgres
        # (batch mode there is just a passthrough), so this stays on for
        # both rather than branching on dialect.
        context.configure(
            connection=connection, target_metadata=target_metadata,
            render_as_batch=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
