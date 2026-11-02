"""Seeding a fresh instance.

Render's free tier has an ephemeral filesystem: the SQLite file is gone after
every restart and redeploy. A dashboard whose first impression is an empty
table is worse than no dashboard, so a fresh instance fills itself.

Seeding runs in the background from the application's lifespan rather than
before the port opens. Blocking startup on two external APIs would mean the
platform waits sixty seconds for a health check that cannot answer yet, and a
cold start that looks like a crash. The page instead says it is still loading
and the data appears underneath it.
"""

import asyncio
import logging
import os

from sqlalchemy import func, select

from depwatch.config import Settings
from depwatch.db import init_db, session_scope
from depwatch.ingest import ingest_advisories, ingest_npm
from depwatch.models import Package

log = logging.getLogger("depwatch.seed")

# A small, recognisable set: widely used, and between them they demonstrate
# every signal the scorer has - current releases, an abandoned package, a
# single-maintainer package, and historic advisories.
SEED_PACKAGES = [
    "express",
    "react",
    "lodash",
    "minimist",
    "axios",
    "chalk",
    "debug",
    "semver",
    "left-pad",
    "request",
]


def package_count() -> int:
    with session_scope() as session:
        return session.scalar(select(func.count()).select_from(Package)) or 0


async def seed(names: list[str] | None = None) -> dict:
    """Ingest metadata then advisories. Idempotent: upserts all the way down."""
    names = names or SEED_PACKAGES
    log.info("seeding %d packages", len(names))
    packages = await ingest_npm(names)
    advisories = await ingest_advisories(names)
    log.info("seed complete: %s packages, %s advisories",
             packages["succeeded"], advisories["advisories"])
    return {"packages": packages, "advisories": advisories}


def seeding_enabled() -> bool:
    """Off in tests and anywhere else that must not touch the network."""
    return os.environ.get("SEED_ON_START", "1") == "1"


def schema_init_enabled() -> bool:
    """Off where the schema is managed from outside the process."""
    return os.environ.get("INIT_DB_ON_START", "1") == "1"


def prepare_schema() -> None:
    """Create the tables before the port opens.

    /healthz counts rows, so the tables have to exist by the time the platform
    sends its first health check - otherwise a perfectly good deploy is marked
    failed because the database it was handed is empty. This one is allowed to
    block startup: it is one local round trip, not two external APIs.

    The URL is read from the environment here rather than taken from the
    import-time settings, because at startup the platform's environment is the
    authority on where the database is.
    """
    if not schema_init_enabled():
        log.info("INIT_DB_ON_START is off, leaving the schema alone")
        return
    init_db(Settings.from_env().database_url)


async def seed_if_empty() -> None:
    """Fill a fresh instance, and leave a populated one alone."""
    if not seeding_enabled():
        log.info("SEED_ON_START is off, skipping seed")
        return
    init_db()
    if package_count() > 0:
        log.info("database already populated, skipping seed")
        return
    try:
        await seed()
    except Exception:
        log.exception("seeding failed; the API will serve whatever landed")


def spawn_seed() -> asyncio.Task:
    """Start seeding without blocking startup."""
    return asyncio.create_task(seed_if_empty())
