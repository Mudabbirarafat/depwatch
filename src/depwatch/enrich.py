"""Enriching packages with GitHub signals.

A separate step from ingestion, deliberately. Ingestion answers "what does the
registry say"; this answers "is anyone still working on it", and the two have
different failure modes, different rate limits and different reasons to be
skipped. Folding it into `ingest_packages` would mean a GitHub outage failing
an npm run.

**The rate limit is the design constraint.** Unauthenticated GitHub allows 60
requests an hour, and each package costs two - so without a token this enriches
thirty packages and then fails for the rest of the hour. With a token it is
5,000. The run says which it is up front rather than discovering it halfway
through, and stops the moment the budget is gone instead of issuing hundreds of
requests that are already doomed.
"""

import asyncio
import logging
from datetime import UTC, datetime

from pydantic import ValidationError
from sqlalchemy import select

from depwatch.db import session_scope
from depwatch.http import FetchError, RateLimitedClient
from depwatch.models import IngestRun, Package
from depwatch.sources import github

log = logging.getLogger("depwatch.enrich")

SOURCE = "github"

# Two requests per package: the repository, and one contributor to read the
# page count from. Without a token the hourly budget is 60.
REQUESTS_PER_PACKAGE = 2
UNAUTHENTICATED_BUDGET = 60


class _Budget:
    """Whether the run should stop asking GitHub anything else.

    Two reasons it should, and both are properties of the run rather than of
    the package being fetched - so they are checked before each request, and
    the remaining packages are skipped rather than each rediscovering the same
    thing:

    * the rate limit is exhausted;
    * the credentials are bad, which produced fifteen identical 401s on the
      deployed instance before this existed. One cause, reported once.
    """

    def __init__(self) -> None:
        self.exhausted = False
        self.reset_at: str | None = None
        self.bad_credentials = False

    @property
    def stop(self) -> bool:
        return self.exhausted or self.bad_credentials

    def note(self, error: FetchError) -> bool:
        """True if this failure was about the run rather than the package."""
        if error.status == 401:
            self.bad_credentials = True
            return True
        remaining = error.headers.get("x-ratelimit-remaining")
        if error.status in (403, 429) and remaining == "0":
            self.exhausted = True
            self.reset_at = error.headers.get("x-ratelimit-reset")
            return True
        return False


def enrichable(ecosystem: str | None = None, db_url: str | None = None) -> list[tuple]:
    """(ecosystem, name, owner, repo) for packages whose repository is on GitHub."""
    with session_scope(db_url) as session:
        rows = session.execute(
            select(Package.ecosystem, Package.name, Package.repository_url).where(
                Package.repository_url.is_not(None),
                *( [Package.ecosystem == ecosystem] if ecosystem else [] ),
            )
        ).all()

    found = []
    for eco, name, url in rows:
        resolved = github.owner_repo(url)
        if resolved:
            found.append((eco, name, *resolved))
    return found


async def _one(
    client: RateLimitedClient,
    budget: _Budget,
    failures: dict[str, str],
    eco: str,
    name: str,
    owner: str,
    repo: str,
) -> dict | None:
    if budget.stop:
        failures[name] = "skipped: the run already stopped"
        return None

    sent = github.headers()
    try:
        document = await client.get_json(github.repo_url(owner, repo), headers=sent)
    except FetchError as exc:
        if budget.note(exc):
            if budget.bad_credentials:
                failures[name] = "GITHUB_TOKEN rejected"
                log.error(
                    "github rejected the token; enrichment cannot run",
                    extra={"status": exc.status},
                )
            else:
                failures[name] = f"rate limited; resets at {budget.reset_at}"
                log.warning(
                    "github rate limit exhausted", extra={"reset_at": budget.reset_at}
                )
        elif exc.status == 404:
            # The registry points at a repository that is gone or private.
            # Worth knowing, not worth failing a run over.
            failures[name] = f"{owner}/{repo} not found"
            log.info("repository not found", extra={"package": name, "repo": f"{owner}/{repo}"})
        else:
            failures[name] = f"HTTP {exc.status} for {owner}/{repo}: {str(exc)[:120]}"
            log.warning(
                "repository fetch failed",
                extra={"package": name, "repo": f"{owner}/{repo}", "status": exc.status,
                       "detail": str(exc)[:200]},
            )
        return None

    try:
        repository = github.parse_repo(document)
    except ValidationError as exc:
        failures[name] = f"unusable payload for {owner}/{repo}"
        log.warning("repository payload unusable", extra={"package": name, "reason": str(exc)[:200]})
        return None

    contributors = None
    if not budget.stop:
        try:
            fetched = await client.get_with_headers(
                github.contributors_url(owner, repo), headers=sent
            )
            contributors = github.contributor_count(
                fetched.payload, fetched.headers.get("link")
            )
        except FetchError as exc:
            # A missing contributor count is not a reason to discard the rest.
            budget.note(exc)

    return {
        "ecosystem": eco,
        "name": name,
        "repo_full_name": repository.full_name,
        "repo_archived": repository.archived,
        "repo_pushed_at": repository.pushed_at,
        "repo_contributors": contributors,
        "repo_stars": repository.stars,
        "repo_open_issues": repository.open_issues,
        "repo_checked_at": datetime.now(UTC),
    }


async def enrich_github(
    ecosystem: str | None = None,
    *,
    limit: int | None = None,
    client: RateLimitedClient | None = None,
    db_url: str | None = None,
) -> dict:
    """Fetch GitHub signals for every package whose repository is there."""
    targets = enrichable(ecosystem, db_url)

    if not github.authenticated():
        affordable = UNAUTHENTICATED_BUDGET // REQUESTS_PER_PACKAGE
        if limit is None or limit > affordable:
            limit = affordable
        log.warning(
            "no GITHUB_TOKEN; limiting the run to what the hourly budget allows",
            extra={"limit": limit, "candidates": len(targets)},
        )
    if limit is not None:
        targets = targets[:limit]

    with session_scope(db_url) as session:
        run = IngestRun(source=SOURCE, requested=len(targets))
        session.add(run)
        session.flush()
        run_id = run.id

    log.info(
        "enrichment started",
        extra={"run_id": run_id, "source": SOURCE, "requested": len(targets),
               "authenticated": github.authenticated()},
    )

    owns_client = client is None
    client = client or RateLimitedClient()
    budget = _Budget()
    failures: dict[str, str] = {}
    try:
        results = await asyncio.gather(
            *(_one(client, budget, failures, *target) for target in targets)
        )
    finally:
        if owns_client:
            await client.aclose()

    rows = [r for r in results if r]

    with session_scope(db_url) as session:
        for row in rows:
            package = session.scalars(
                select(Package).where(
                    Package.ecosystem == row["ecosystem"], Package.name == row["name"]
                )
            ).first()
            if package is None:
                continue
            for column, value in row.items():
                if column not in ("ecosystem", "name"):
                    setattr(package, column, value)

        run = session.get(IngestRun, run_id)
        run.succeeded = len(rows)
        run.failed = len(targets) - len(rows)
        run.finished_at = datetime.now(UTC)
        run.status = "succeeded" if len(rows) == len(targets) else "partial"
        if budget.bad_credentials:
            run.status = "partial"
            # One sentence someone can act on, rather than fifteen copies of
            # the same rejection.
            run.error = (
                "GITHUB_TOKEN was rejected by GitHub (401 Bad credentials). "
                "Regenerate it and set it again; no scopes are needed for "
                "public repositories."
            )
        elif budget.exhausted:
            run.status = "partial"
            run.error = f"github rate limit exhausted; resets at {budget.reset_at}"
        elif failures:
            # A run where everything failed used to record no reason at all,
            # which made diagnosing it a matter of reading logs by hand. The
            # ingest runs have always done this; this one should too.
            run.error = "; ".join(f"{n}: {why}" for n, why in list(failures.items())[:5])

    summary = {
        "run_id": run_id,
        "requested": len(targets),
        "enriched": len(rows),
        "archived": sum(1 for r in rows if r["repo_archived"]),
        "rate_limited": budget.exhausted,
        "bad_credentials": budget.bad_credentials,
    }
    log.info("enrichment finished", extra={"source": SOURCE, **summary})
    return summary
