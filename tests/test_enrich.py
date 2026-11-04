"""GitHub enrichment as a run: what it skips, what it stores, when it stops."""

from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import select

from depwatch.db import init_db, session_scope
from depwatch.enrich import enrich_github, enrichable
from depwatch.models import IngestRun, Package
from depwatch.scores import scoring_row


@pytest.fixture(autouse=True)
def token(monkeypatch):
    """Authenticated by default; the unauthenticated cap has its own test."""
    monkeypatch.setenv("GITHUB_TOKEN", "a-test-token")


@pytest.fixture
def db(tmp_path):
    init_db(f"sqlite:///{tmp_path / 'enrich.db'}")


def add(name, url, ecosystem="npm"):
    with session_scope() as session:
        session.add(
            Package(
                ecosystem=ecosystem, name=name, latest_version="1.0.0",
                deprecated=False, repository_url=url,
            )
        )


def stored(name, ecosystem="npm") -> dict:
    with session_scope() as session:
        package = session.scalars(
            select(Package).where(Package.ecosystem == ecosystem, Package.name == name)
        ).one()
        return {
            "repo_full_name": package.repo_full_name,
            "repo_archived": package.repo_archived,
            "repo_contributors": package.repo_contributors,
            "repo_stars": package.repo_stars,
            "repo_pushed_at": package.repo_pushed_at,
            "repo_checked_at": package.repo_checked_at,
        }


def responder(repos: dict, *, rate_limited: set | None = None, missing: set | None = None):
    """A GitHub stand-in: /repos/{o}/{r} and its contributors list."""
    rate_limited = rate_limited or set()
    missing = missing or set()

    def handle(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        parts = path.strip("/").split("/")
        key = f"{parts[1]}/{parts[2]}"

        if key in rate_limited:
            return httpx.Response(
                403,
                headers={"x-ratelimit-remaining": "0", "x-ratelimit-reset": "1762000000"},
                json={"message": "API rate limit exceeded"},
            )
        if key in missing:
            return httpx.Response(404, json={"message": "Not Found"})

        if path.endswith("/contributors"):
            count = repos[key].get("contributors", 1)
            headers = {}
            if count > 1:
                headers["link"] = (
                    f'<https://api.github.com/x?per_page=1&page=2>; rel="next", '
                    f'<https://api.github.com/x?per_page=1&page={count}>; rel="last"'
                )
            return httpx.Response(200, headers=headers, json=[{"login": "someone"}])

        return httpx.Response(200, json={"full_name": key, **repos[key]})

    return handle


async def test_only_github_hosted_packages_are_enrichable(db):
    add("a", "https://github.com/owner/a")
    add("b", "https://gitlab.com/owner/b")
    add("c", None)
    add("d", "git+ssh://git@github.com/owner/d.git")

    found = {name for _, name, _, _ in enrichable()}
    assert found == {"a", "d"}


async def test_the_signals_are_stored(db, mock_client):
    add("a", "https://github.com/owner/a")
    repos = {
        "owner/a": {
            "archived": True,
            "pushed_at": "2026-10-01T00:00:00Z",
            "stargazers_count": 1234,
            "open_issues_count": 9,
            "contributors": 43,
        }
    }

    summary = await enrich_github(client=mock_client(responder(repos)))

    assert summary["enriched"] == 1
    assert summary["archived"] == 1
    row = stored("a")
    assert row["repo_full_name"] == "owner/a"
    assert row["repo_archived"] is True
    assert row["repo_contributors"] == 43
    assert row["repo_stars"] == 1234
    assert row["repo_checked_at"] is not None


async def test_a_missing_repository_does_not_fail_the_others(db, mock_client):
    """A registry pointing at a deleted or private repository is common."""
    add("a", "https://github.com/owner/a")
    add("gone", "https://github.com/owner/gone")
    repos = {"owner/a": {"contributors": 2}}

    summary = await enrich_github(
        client=mock_client(responder(repos, missing={"owner/gone"}))
    )

    assert summary["enriched"] == 1
    assert summary["requested"] == 2
    assert stored("a")["repo_full_name"] == "owner/a"
    assert stored("gone")["repo_checked_at"] is None


async def test_an_exhausted_rate_limit_stops_the_run(db, mock_client):
    """Once the budget is gone every remaining request is already doomed, so
    the run stops rather than issuing hundreds of them."""
    for i in range(6):
        add(f"p{i}", f"https://github.com/owner/p{i}")

    summary = await enrich_github(
        client=mock_client(responder({}, rate_limited={f"owner/p{i}" for i in range(6)}))
    )

    assert summary["rate_limited"] is True
    assert summary["enriched"] == 0

    with session_scope() as session:
        run = session.scalars(
            select(IngestRun).where(IngestRun.source == "github")
        ).one()
        assert run.status == "partial"
        assert "rate limit" in run.error


async def test_an_unauthenticated_run_caps_itself(db, mock_client, monkeypatch):
    """60 requests an hour at two per package is thirty packages, and the run
    says so up front rather than failing most of the way through."""
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    for i in range(40):
        add(f"p{i}", f"https://github.com/owner/p{i}")
    repos = {f"owner/p{i}": {"contributors": 1} for i in range(40)}

    summary = await enrich_github(client=mock_client(responder(repos)))

    assert summary["requested"] == 30
    assert summary["enriched"] == 30


async def test_the_run_is_recorded_like_any_other(db, mock_client):
    add("a", "https://github.com/owner/a")

    await enrich_github(client=mock_client(responder({"owner/a": {"contributors": 1}})))

    with session_scope() as session:
        run = session.scalars(select(IngestRun).where(IngestRun.source == "github")).one()
        assert run.status == "succeeded"
        assert run.requested == 1
        assert run.succeeded == 1
        assert run.finished_at is not None


async def test_enrichment_is_scoped_to_one_ecosystem(db, mock_client):
    add("a", "https://github.com/owner/a", ecosystem="npm")
    add("b", "https://github.com/owner/b", ecosystem="pypi")
    repos = {"owner/a": {"contributors": 1}, "owner/b": {"contributors": 1}}

    await enrich_github("pypi", client=mock_client(responder(repos)))

    assert stored("b", "pypi")["repo_checked_at"] is not None
    assert stored("a", "npm")["repo_checked_at"] is None


def test_the_scorer_actually_receives_the_signals(db):
    """A regression test for a bug that produced no error at all.

    The enrichment columns populated and the rules that read them existed, but
    the projection handed to the scorer did not carry them - so every signal
    was inert and every score was unchanged. Nothing failed; it simply did
    nothing.
    """
    with session_scope() as session:
        session.add(
            Package(
                ecosystem="npm", name="a", latest_version="1.0.0", deprecated=False,
                repo_archived=True,
                repo_contributors=1,
                repo_pushed_at=datetime.now(UTC) - timedelta(days=900),
            )
        )
        package = session.scalars(select(Package).where(Package.name == "a")).one()
        row = scoring_row(package)

    assert row["repo_archived"] is True
    assert row["repo_contributors"] == 1
    assert row["repo_pushed_at"] is not None


async def test_a_run_that_fails_records_why(db, mock_client):
    """A run where every package failed used to record no reason at all.

    `status: partial, succeeded: 0, failed: 15, error: null` is not a
    diagnosis, and it made a live failure a matter of reading logs by hand.
    The ingest runs have always recorded their failures; this one does now.
    """
    add("a", "https://github.com/owner/a")
    add("gone", "https://github.com/owner/gone")

    await enrich_github(client=mock_client(responder({}, missing={"owner/a", "owner/gone"})))

    with session_scope() as session:
        run = session.scalars(select(IngestRun).where(IngestRun.source == "github")).one()
        assert run.succeeded == 0
        assert run.failed == 2
        assert run.error is not None
        assert "not found" in run.error


async def test_a_rate_limited_run_says_so_rather_than_listing_packages(db, mock_client):
    """One cause, not fifteen symptoms."""
    for i in range(3):
        add(f"p{i}", f"https://github.com/owner/p{i}")

    await enrich_github(
        client=mock_client(responder({}, rate_limited={f"owner/p{i}" for i in range(3)}))
    )

    with session_scope() as session:
        run = session.scalars(select(IngestRun).where(IngestRun.source == "github")).one()
        assert "rate limit exhausted" in run.error


async def test_a_successful_run_records_no_error(db, mock_client):
    add("a", "https://github.com/owner/a")

    await enrich_github(client=mock_client(responder({"owner/a": {"contributors": 1}})))

    with session_scope() as session:
        run = session.scalars(select(IngestRun).where(IngestRun.source == "github")).one()
        assert run.status == "succeeded"
        assert run.error is None


async def test_a_rejected_token_stops_the_run_with_one_clear_reason(db, mock_client):
    """The live failure: fifteen packages, fifteen identical 401s, and an
    error field that said nothing actionable.

    Bad credentials are a property of the run, not of any package, so the
    first one stops it and the reason says what to do.
    """
    for i in range(5):
        add(f"p{i}", f"https://github.com/owner/p{i}")

    def unauthorised(request):
        return httpx.Response(401, json={"message": "Bad credentials"})

    summary = await enrich_github(client=mock_client(unauthorised))

    assert summary["bad_credentials"] is True
    assert summary["enriched"] == 0

    with session_scope() as session:
        run = session.scalars(select(IngestRun).where(IngestRun.source == "github")).one()
        assert "GITHUB_TOKEN was rejected" in run.error
        assert "Regenerate it" in run.error
        # One cause, not five copies of it.
        assert run.error.count("401") <= 1
