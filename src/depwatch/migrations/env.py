"""Alembic environment.

The migrations live inside the package rather than beside it so they ship in
the wheel: the deployed instance runs `upgrade head` on boot, and it can only
do that if the revision files are installed with the code.

The URL comes from the environment at call time, not from alembic.ini, because
the platform is what knows where the database is.
"""

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from depwatch.config import Settings
from depwatch.models import Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# A URL passed in programmatically (the application calling upgrade on boot,
# or a test pointing at a temporary file) wins; otherwise fall back to the
# environment. alembic.ini deliberately sets no URL at all.
if not config.get_main_option("sqlalchemy.url", None):
    config.set_main_option("sqlalchemy.url", Settings.from_env().database_url)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
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
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            # SQLite cannot ALTER most things in place; batch mode rebuilds the
            # table instead, so one migration works on both backends.
            render_as_batch=connection.dialect.name == "sqlite",
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
