"""The gates.

Each one gets a warehouse built to fail it, because a gate that cannot fail is
a gate that proves nothing.
"""

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from depwatch.api import app
from depwatch.db import init_db, session_scope
from depwatch.gates import as_utc, evaluate
from depwatch.models import Advisory, IngestRun, Package
from depwatch.scores import rescore

NOW = datetime(2026, 11, 2, 12, 0, tzinfo=UTC)


@pytest.fixture
def db(tmp_path):
    init_db(f"sqlite:///{tmp_path / 'gates.db'}")
    return tmp_path


def add_run(*, finished_at, status="succeeded", requested=10, succeeded=10, quarantined=0, failed=0):
    with session_scope() as session:
        session.add(
            IngestRun(
                source="npm", status=status, finished_at=finished_at,
                requested=requested, succeeded=succeeded,
                quarantined=quarantined, failed=failed,
            )
        )


def add_data(packages=1, advisories=1):
    with session_scope() as session:
        for i in range(packages):
            session.add(
                Package(ecosystem="npm", name=f"pkg-{i}", latest_version="1.0.0", deprecated=False)
            )
        for i in range(advisories):
            session.add(
                Advisory(ecosystem="npm", package_name="pkg-0", osv_id=f"GHSA-{i}", severity="LOW")
            )


def gate(report: dict, name: str) -> dict:
    return next(g for g in report["gates"] if g["name"] == name)


def test_a_healthy_warehouse_passes_everything(db):
    add_data()
    add_run(finished_at=NOW - timedelta(hours=2))
    # A healthy warehouse includes materialised scores: every path that writes
    # packages rescores, so one without them is not healthy.
    rescore()

    report = evaluate(now=NOW)
    assert report["passed"] is True, report["gates"]


def test_an_empty_warehouse_fails(db):
    add_run(finished_at=NOW - timedelta(hours=1))
    report = evaluate(now=NOW)

    assert report["passed"] is False
    assert gate(report, "warehouse_has_data")["passed"] is False


def test_no_advisories_at_all_is_treated_as_suspicious(db):
    """Any realistic package set contains something with a history, so a clean
    sweep is likelier to be a broken query than a safe ecosystem."""
    add_data(packages=5, advisories=0)
    add_run(finished_at=NOW - timedelta(hours=1))

    report = evaluate(now=NOW)
    assert gate(report, "advisories_were_fetched")["passed"] is False


def test_stale_data_fails_the_freshness_gate(db):
    add_data()
    add_run(finished_at=NOW - timedelta(hours=40))

    report = evaluate(now=NOW)
    assert gate(report, "data_is_fresh")["passed"] is False
    assert "40.0h" in gate(report, "data_is_fresh")["detail"]


def test_too_many_failures_fails(db):
    add_data()
    add_run(finished_at=NOW - timedelta(hours=1), requested=10, succeeded=5, failed=5)

    report = evaluate(now=NOW)
    assert gate(report, "failures_within_limit")["passed"] is False
    assert "5 of 10 (50%)" in gate(report, "failures_within_limit")["detail"]


def test_too_much_quarantine_fails(db):
    add_data()
    add_run(finished_at=NOW - timedelta(hours=1), requested=10, succeeded=7, quarantined=3)

    report = evaluate(now=NOW)
    assert gate(report, "quarantine_within_limit")["passed"] is False


def test_a_run_that_requested_nothing_is_not_a_failure(db):
    """0 of 0 is honestly a pass; reporting it as a rate breach is noise."""
    add_data()
    add_run(finished_at=NOW - timedelta(hours=1), requested=0, succeeded=0)

    report = evaluate(now=NOW)
    assert gate(report, "failures_within_limit")["passed"] is True


def test_an_instance_with_no_finished_run_fails_rather_than_crashing(db):
    add_data()
    report = evaluate(now=NOW)

    assert report["passed"] is False
    assert gate(report, "data_is_fresh")["detail"] == "no run has finished yet"


def test_a_run_still_in_flight_does_not_count_as_evidence(db):
    """It has no finished_at and may yet fail."""
    add_data()
    with session_scope() as session:
        session.add(IngestRun(source="npm", status="running", requested=10))

    report = evaluate(now=NOW)
    assert gate(report, "data_is_fresh")["passed"] is False


def test_thresholds_come_from_the_environment(db, monkeypatch):
    add_data()
    add_run(finished_at=NOW - timedelta(hours=40))
    assert gate(evaluate(now=NOW), "data_is_fresh")["passed"] is False

    monkeypatch.setenv("MAX_DATA_AGE_HOURS", "48")
    assert gate(evaluate(now=NOW), "data_is_fresh")["passed"] is True


def test_a_nonsense_threshold_falls_back_to_the_default(db, monkeypatch):
    add_data()
    add_run(finished_at=NOW - timedelta(hours=2))
    monkeypatch.setenv("MAX_DATA_AGE_HOURS", "not-a-number")

    assert gate(evaluate(now=NOW), "data_is_fresh")["passed"] is True


def test_naive_timestamps_are_read_as_utc():
    """SQLite has no timezone type, so a timezone=True column comes back naive
    from SQLite and aware from Postgres. Comparing the naive one against an
    aware now() raises - which would break the gate on exactly one backend."""
    naive = datetime(2026, 11, 2, 10, 0)
    assert as_utc(naive) == datetime(2026, 11, 2, 10, 0, tzinfo=UTC)
    assert as_utc(NOW) == NOW
    assert as_utc(None) is None


def test_the_endpoint_answers_503_when_a_gate_fails(db):
    """A caller that only reads the status code must not mistake stale data
    for fresh data."""
    client = TestClient(app)
    response = client.get("/gates")

    assert response.status_code == 503
    assert response.json()["passed"] is False
    # The report still comes back: a monitor needs to know *which* gate failed.
    assert gate(response.json(), "warehouse_has_data")["passed"] is False


def test_the_endpoint_answers_200_when_everything_passes(db):
    add_data()
    add_run(finished_at=datetime.now(UTC) - timedelta(minutes=5))
    rescore()

    response = TestClient(app).get("/gates")
    assert response.status_code == 200
    assert response.json()["passed"] is True


def test_healthz_does_not_fail_just_because_the_data_is_stale(db):
    """The platform restarts an instance whose health check fails. Stale data
    is not a reason to restart a working server."""
    add_data()
    add_run(finished_at=datetime.now(UTC) - timedelta(days=30))
    client = TestClient(app)

    assert client.get("/gates").status_code == 503
    assert client.get("/healthz").status_code == 200


def test_an_enrichment_run_is_not_judged_as_ingestion(db):
    """Found in production: a GitHub enrichment run where every package failed
    took the live gates to 503, over data that was perfectly fine.

    These gates ask whether the warehouse is being filled correctly.
    Enrichment answers a different question and has a different idea of
    failure - a package hosted on GitLab, a deleted repository, an exhausted
    API budget - so it must not set the ingestion thresholds.
    """
    add_data()
    add_run(finished_at=NOW - timedelta(hours=2), requested=10, succeeded=10, failed=0)
    rescore()
    # ... and then an enrichment run where everything failed, more recently.
    with session_scope() as session:
        session.add(
            IngestRun(
                source="github", status="partial", finished_at=NOW - timedelta(minutes=5),
                requested=15, succeeded=0, failed=15,
            )
        )

    report = evaluate(now=NOW)

    assert report["passed"] is True, report["gates"]
    assert gate(report, "failures_within_limit")["passed"] is True
    assert report["last_finished_run"]["source"] == "npm"


def test_a_failing_ingest_run_is_still_caught_behind_an_enrichment_run(db):
    """The exclusion must not become a way for a real failure to hide."""
    add_data()
    add_run(finished_at=NOW - timedelta(hours=2), requested=10, succeeded=2, failed=8)
    rescore()
    with session_scope() as session:
        session.add(
            IngestRun(
                source="github", status="succeeded", finished_at=NOW - timedelta(minutes=5),
                requested=5, succeeded=5, failed=0,
            )
        )

    report = evaluate(now=NOW)
    assert gate(report, "failures_within_limit")["passed"] is False
