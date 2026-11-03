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
from depwatch.db import engine, session_scope
from depwatch.ingest import ingest_advisories, ingest_packages
from depwatch.migrate import upgrade_to_head
from depwatch.models import Advisory, Package
from depwatch.scores import rescore

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

# The same idea on the other side: widely used, and between them they cover
# every signal - current releases, an abandoned project, an organisation-owned
# one, and packages with long advisory histories.
SEED_PYPI_PACKAGES = [
    "requests",
    "django",
    "flask",
    "urllib3",
    "pyyaml",
    "pillow",
    "numpy",
    "distribute",
]

SEED_SETS = {"npm": SEED_PACKAGES, "pypi": SEED_PYPI_PACKAGES}


def package_count() -> int:
    with session_scope() as session:
        return session.scalar(select(func.count()).select_from(Package)) or 0


def advisory_count() -> int:
    with session_scope() as session:
        return session.scalar(select(func.count()).select_from(Advisory)) or 0


async def seed(names: list[str] | None = None, ecosystem: str | None = None) -> dict:
    """Ingest metadata then advisories, for every seeded ecosystem.

    Both of them, because the dashboard offers both and a registry tab that
    opens onto an empty table reads as broken rather than as unseeded.

    Idempotent: upserts all the way down.
    """
    if names:
        sets = {ecosystem or "npm": names}
    elif ecosystem:
        sets = {ecosystem: SEED_SETS[ecosystem]}
    else:
        sets = SEED_SETS

    results: dict[str, dict] = {}
    for eco, eco_names in sets.items():
        log.info("seeding %d packages", len(eco_names), extra={"ecosystem": eco})
        packages = await ingest_packages(eco_names, ecosystem=eco)
        advisories = await ingest_advisories(eco_names, ecosystem=eco)
        results[eco] = {"packages": packages, "advisories": advisories}

    scored = rescore()
    log.info(
        "seed complete: %s ecosystems, %s scored",
        len(results), scored["scored"],
    )
    return {**results, "scored": scored}


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

    It runs the migrations rather than `create_all()`, so the deployed schema
    has a version and the next change is a revision instead of a manual ALTER.

    The URL is read from the environment here rather than taken from the
    import-time settings, because at startup the platform's environment is the
    authority on where the database is.
    """
    if not schema_init_enabled():
        log.info("INIT_DB_ON_START is off, leaving the schema alone")
        return
    url = Settings.from_env().database_url
    engine(url)  # bind before anything queries
    upgrade_to_head(url)  # creates the directory too, if SQLite needs one


async def seed_if_empty() -> None:
    """Fill a fresh instance, and leave a populated one alone."""
    if not seeding_enabled():
        log.info("SEED_ON_START is off, skipping seed")
        return
    # Deliberately does not build the schema: migrations are the only path to
    # one, and prepare_schema has already run by the time this does.
    #
    # "Populated" has to mean both halves ran. Seeding fetches npm first and
    # OSV second, so an instance replaced between the two comes back with
    # packages and no advisories - and a check on packages alone then declares
    # that finished and never repairs it. Found in production by the gate that
    # asks whether any advisories were fetched at all.
    packages, advisories = package_count(), advisory_count()
    if packages > 0 and advisories > 0:
        log.info(
            "database already populated, skipping seed",
            extra={"packages": packages, "advisories": advisories},
        )
        return
    if packages > 0:
        log.warning(
            "packages present but no advisories; seeding again to finish the job",
            extra={"packages": packages},
        )
    try:
        await seed()
    except Exception:
        log.exception("seeding failed; the API will serve whatever landed")


def spawn_seed() -> asyncio.Task:
    """Start seeding without blocking startup."""
    return asyncio.create_task(seed_if_empty())
