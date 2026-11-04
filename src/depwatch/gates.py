"""Freshness and quality gates.

Every run already records what it saw. Nothing read those numbers, which makes
them decoration: a pipeline that quietly stops ingesting, or starts
quarantining half of what it fetches, looks exactly like a healthy one from the
outside. These turn the run metadata into a pass or fail.

They are deliberately about the data rather than the process. "The server is
up" is `/healthz`; "the server is up and serving three-week-old numbers it got
from one successful run out of nine attempts" is what these catch.

Thresholds come from the environment so the same code can gate a daily
schedule and an hourly one.
"""

import os
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select

from depwatch.db import session_scope
from depwatch.models import Advisory, IngestRun, Package

# A daily schedule plus room for one missed run and a slow cold start.
DEFAULT_MAX_AGE_HOURS = 26.0
DEFAULT_MAX_FAILURE_RATE = 0.2
DEFAULT_MAX_QUARANTINE_RATE = 0.1

# A run that finished is evidence; a run still in flight is not.
FINISHED_STATUSES = ("succeeded", "partial")

# Sources that enrich rather than ingest.
#
# These gates ask "is the warehouse being filled correctly", and enrichment
# answers a different question with a different idea of failure: a package
# hosted on GitLab, a deleted repository, an exhausted API budget. Those are
# expected, and judging them by the ingestion thresholds took the whole site
# to 503 over data that was perfectly fine. Found in production, by exactly
# that happening.
ENRICHMENT_SOURCES = ("github",)


@dataclass(frozen=True)
class Gate:
    name: str
    passed: bool
    detail: str

    def as_dict(self) -> dict:
        return {"name": self.name, "passed": self.passed, "detail": self.detail}


def _float_from_env(name: str, default: float) -> float:
    try:
        return float(os.environ[name])
    except (KeyError, ValueError):
        return default


def as_utc(moment: datetime | None) -> datetime | None:
    """Treat a naive timestamp as UTC.

    SQLite has no timezone type, so a `DateTime(timezone=True)` column comes
    back naive from SQLite and aware from Postgres. Comparing the naive one
    against an aware `now` raises, which would make the freshness gate fail by
    crashing on exactly one of the two backends this is meant to run on.
    """
    if moment is None:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


def evaluate(now: datetime | None = None) -> dict:
    """Run every gate. Returns the report and whether all of them passed."""
    now = now or datetime.now(UTC)
    max_age = timedelta(hours=_float_from_env("MAX_DATA_AGE_HOURS", DEFAULT_MAX_AGE_HOURS))
    max_failures = _float_from_env("MAX_FAILURE_RATE", DEFAULT_MAX_FAILURE_RATE)
    max_quarantine = _float_from_env("MAX_QUARANTINE_RATE", DEFAULT_MAX_QUARANTINE_RATE)

    with session_scope() as session:
        packages = session.scalar(select(func.count()).select_from(Package)) or 0
        advisories = session.scalar(select(func.count()).select_from(Advisory)) or 0
        unscored = (
            session.scalar(
                select(func.count()).select_from(Package).where(Package.score.is_(None))
            )
            or 0
        )
        last_finished = session.scalars(
            select(IngestRun)
            .where(
                IngestRun.status.in_(FINISHED_STATUSES),
                IngestRun.finished_at.is_not(None),
                IngestRun.source.not_in(ENRICHMENT_SOURCES),
            )
            .order_by(IngestRun.finished_at.desc())
            .limit(1)
        ).first()
        last = (
            {
                "source": last_finished.source,
                "status": last_finished.status,
                "finished_at": as_utc(last_finished.finished_at),
                "requested": last_finished.requested,
                "succeeded": last_finished.succeeded,
                "quarantined": last_finished.quarantined,
                "failed": last_finished.failed,
            }
            if last_finished
            else None
        )

    gates = [
        _warehouse_has_data(packages),
        _advisories_were_fetched(advisories),
        _everything_is_scored(packages, unscored),
    ]

    if last is None:
        gates.append(Gate("data_is_fresh", False, "no run has finished yet"))
        gates.append(Gate("failures_within_limit", False, "no run has finished yet"))
        gates.append(Gate("quarantine_within_limit", False, "no run has finished yet"))
    else:
        age = now - last["finished_at"]
        gates.append(_data_is_fresh(age, max_age))
        gates.append(_rate_gate("failures_within_limit", last["failed"], last["requested"], max_failures))
        gates.append(
            _rate_gate("quarantine_within_limit", last["quarantined"], last["requested"], max_quarantine)
        )

    return {
        "passed": all(gate.passed for gate in gates),
        "gates": [gate.as_dict() for gate in gates],
        "checked_at": now.isoformat(),
        "last_finished_run": (
            {**last, "finished_at": last["finished_at"].isoformat()} if last else None
        ),
    }


def _warehouse_has_data(packages: int) -> Gate:
    return Gate(
        "warehouse_has_data",
        packages > 0,
        f"{packages} packages" if packages else "the warehouse is empty",
    )


def _advisories_were_fetched(advisories: int) -> Gate:
    """Zero advisories across every package means OSV was not really answering.

    Any realistic package set contains something with a history; a clean sweep
    is far more likely to be a broken query than a safe ecosystem.
    """
    return Gate(
        "advisories_were_fetched",
        advisories > 0,
        f"{advisories} advisories" if advisories else "no advisories at all, which is suspicious",
    )


def _everything_is_scored(packages: int, unscored: int) -> Gate:
    """The materialised score is derived, so it can silently fall behind.

    A package with no score is served as `unscored` and sorts last, so it is
    not dangerous - but it does mean the ordering is incomplete, and nothing
    else would ever mention it.
    """
    if packages == 0:
        return Gate("packages_are_scored", False, "the warehouse is empty")
    return Gate(
        "packages_are_scored",
        unscored == 0,
        "all scored" if unscored == 0 else f"{unscored} of {packages} have no stored score",
    )


def _data_is_fresh(age: timedelta, limit: timedelta) -> Gate:
    hours = age.total_seconds() / 3600
    return Gate(
        "data_is_fresh",
        age <= limit,
        f"last finished run was {hours:.1f}h ago, limit {limit.total_seconds() / 3600:.0f}h",
    )


def _rate_gate(name: str, bad: int, requested: int, limit: float) -> Gate:
    if requested <= 0:
        # Nothing was asked for, so nothing can have gone wrong. Reporting a
        # rate of 0/0 as a pass is the honest reading.
        return Gate(name, True, "the run requested nothing")
    rate = bad / requested
    return Gate(
        name,
        rate <= limit,
        f"{bad} of {requested} ({rate:.0%}), limit {limit:.0%}",
    )
