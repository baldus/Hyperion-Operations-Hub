import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from invapp.extensions import db

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = db.Model.metadata


def _get_database_url() -> str | None:
    url = config.get_main_option("sqlalchemy.url")
    if url and not url.startswith("env://"):
        return url
    env_key = None
    if url and url.startswith("env://"):
        env_key = url.split("env://", 1)[1]
    if not env_key:
        env_key = "DB_URL"
    return os.getenv(env_key)


def run_migrations_offline() -> None:
    url = _get_database_url()
    if not url:
        raise RuntimeError("Database URL must be provided via DB_URL for offline migrations")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    section = config.get_section(config.config_ini_section) or {}
    url = _get_database_url()
    if url:
        section["sqlalchemy.url"] = url

    connectable = engine_from_config(
        section,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
