"""Materialising the score.

`risk.score_package` is a pure function with no database and no clock beyond
the one passed in, which is what makes every weight in it testable. The cost of
that purity is that risk cannot be expressed in SQL, so ordering packages by it
meant loading and scoring every package in the ecosystem before the worst ten
could be returned - O(packages) per request, for a page of ten.

This writes the answer down. `/packages` becomes an indexed ORDER BY ... LIMIT,
and the scoring function stays pure.

The trade is the usual one for a materialised view: the stored score can be
stale. Two things keep that honest - `scored_at` records when it was computed,
and a gate reports any package carrying no score at all.
"""

import logging
from datetime import UTC, datetime

from sqlalchemy import select

from depwatch.db import session_scope
from depwatch.models import Advisory, Package
from depwatch.risk import score_package

log = logging.getLogger("depwatch.scores")


def scoring_row(package: Package) -> dict:
    """Everything the scorer reads, in one place.

    Shared with the API rather than duplicated, because the two drifting apart
    is not a visible failure: the GitHub enrichment columns populated and the
    rules that read them existed, but this projection did not carry them, so
    the signals were inert and every score was unchanged. Nothing errored.
    """
    return {
        "name": package.name,
        "ecosystem": package.ecosystem,
        "latest_version": package.latest_version,
        "license": package.license,
        "maintainers": package.maintainers,
        "deprecated": package.deprecated,
        "last_published_at": package.last_published_at,
        "repo_archived": package.repo_archived,
        "repo_pushed_at": package.repo_pushed_at,
        "repo_contributors": package.repo_contributors,
    }


def rescore(ecosystem: str | None = None, db_url: str | None = None) -> dict:
    """Recompute and store the score for every package, at its latest version.

    One pass over the packages and one over their advisories, rather than a
    query per package: the whole point is to stop doing O(packages) work per
    request, so doing it per package here would just move the problem.
    """
    started = datetime.now(UTC)

    with session_scope(db_url) as session:
        packages = session.scalars(
            select(Package).where(Package.ecosystem == ecosystem)
            if ecosystem
            else select(Package)
        ).all()
        if not packages:
            return {"ecosystem": ecosystem, "scored": 0}

        advisories = session.scalars(
            select(Advisory).where(Advisory.ecosystem == ecosystem)
            if ecosystem
            else select(Advisory)
        ).all()

        by_package: dict[tuple[str, str], list[dict]] = {}
        for advisory in advisories:
            by_package.setdefault((advisory.ecosystem, advisory.package_name), []).append(
                {
                    "osv_id": advisory.osv_id,
                    "severity": advisory.severity,
                    "fixed_version": advisory.fixed_version,
                    "last_affected": advisory.last_affected,
                }
            )

        bands: dict[str, int] = {}
        for package in packages:
            found = by_package.get((package.ecosystem, package.name), [])
            result = score_package(scoring_row(package), found)
            package.score = result.score
            package.band = result.band
            package.reasons = list(result.reasons)
            package.scored_at = started
            bands[result.band] = bands.get(result.band, 0) + 1

    summary = {"ecosystem": ecosystem, "scored": len(packages), **bands}
    log.info("rescored packages", extra=summary)
    return summary
