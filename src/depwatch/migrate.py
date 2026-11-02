"""Running migrations from inside the application.

The deployed instance gets no shell step: the platform's start command is the
server itself, so the process has to bring its own schema up. This is the same
`alembic upgrade head` the CLI runs, called programmatically against the
migration files packaged alongside the code - which is why they live inside the
package rather than beside it, and why `pyproject.toml` ships them.
"""

import logging
from pathlib import Path

from alembic import command
from alembic.config import Config

from depwatch.config import Settings
from depwatch.db import ensure_parent_directory

log = logging.getLogger("depwatch.migrate")

MIGRATIONS_DIR = Path(__file__).parent / "migrations"


def alembic_config(url: str | None = None) -> Config:
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    config.set_main_option("sqlalchemy.url", url or Settings.from_env().database_url)
    return config


def upgrade_to_head(url: str | None = None) -> None:
    """Apply every migration that has not run yet. A no-op when up to date.

    Creates the directory a SQLite file lives in first. That belongs here
    rather than only in the caller: this is the one function every path to a
    schema goes through, and SQLite does not create a missing directory - it
    reports "unable to open database file", which reads as a broken
    configuration rather than a missing folder.
    """
    target = url or Settings.from_env().database_url
    ensure_parent_directory(target)
    config = alembic_config(target)
    log.info("applying migrations", extra={"target": "head"})
    command.upgrade(config, "head")


def downgrade_to(revision: str, url: str | None = None) -> None:
    command.downgrade(alembic_config(url), revision)
