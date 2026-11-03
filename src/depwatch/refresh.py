"""Re-ingesting what the warehouse already knows.

Ingestion was a thing someone ran. This makes it a thing that happens: the
scheduled workflow calls `/refresh`, which re-fetches every package already in
the warehouse so the data has a reason to be current beyond "the instance
restarted recently".

Which packages: the ones already known, because that is what going stale
means. An empty warehouse falls back to the seed set, so a cold instance that
has not finished seeding still gets somewhere.
"""

import asyncio
import logging

from sqlalchemy import select

from depwatch.db import session_scope
from depwatch.ingest import ingest_advisories, ingest_npm
from depwatch.models import Package
from depwatch.scores import rescore
from depwatch.seed import SEED_PACKAGES

log = logging.getLogger("depwatch.refresh")


def known_names(ecosystem: str = "npm") -> list[str]:
    with session_scope() as session:
        names = session.scalars(
            select(Package.name).where(Package.ecosystem == ecosystem).order_by(Package.name)
        ).all()
    return list(names)


async def refresh(names: list[str] | None = None) -> dict:
    """Re-ingest metadata and advisories. Idempotent, like every other write."""
    names = names or known_names() or SEED_PACKAGES
    log.info("refresh started", extra={"packages": len(names)})
    packages = await ingest_npm(names)
    advisories = await ingest_advisories(names)
    # The stored score is derived from both of those, so it has to follow them
    # or /packages would serve an ordering based on the previous run's data.
    scored = rescore()
    summary = {"packages": packages, "advisories": advisories, "scored": scored}
    log.info(
        "refresh finished",
        extra={
            "packages": packages["succeeded"],
            "advisories": advisories["advisories"],
            "failed": packages["failed"] + advisories["failed"],
        },
    )
    return summary


class _State:
    """Whether a refresh is in flight, and what the last one did."""

    def __init__(self) -> None:
        self.running = False
        self.last_summary: dict | None = None
        self.last_error: str | None = None


state = _State()


async def _run(names: list[str] | None) -> None:
    try:
        state.last_summary = await refresh(names)
        state.last_error = None
    except Exception as exc:
        state.last_error = str(exc)
        log.exception("refresh failed")
    finally:
        state.running = False


def start(names: list[str] | None = None) -> asyncio.Task | None:
    """Begin a refresh unless one is already running. None means 'already running'.

    The flag is set before the task is created and there is no `await` between
    the check and the set, so on a single event loop two overlapping requests
    cannot both get past it. A lock would say the same thing less directly, and
    a second refresh is not something to queue - it is something to decline.
    """
    if state.running:
        return None
    state.running = True
    return asyncio.create_task(_run(names))
