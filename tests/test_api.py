"""The API: /healthz, /analyse, /packages.

These run against a real (temporary) database rather than mocks, so they
exercise the queries as well as the handlers.
"""

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from depwatch.api import app, names_from_manifest
from depwatch.db import init_db, session_scope
from depwatch.models import Advisory, Package


@pytest.fixture
def client(tmp_path):
    # init_db rebinds the module-level engine, so the app's session_scope()
    # picks up this database without the handlers knowing about tests.
    init_db(f"sqlite:///{tmp_path / 'api.db'}")
    return TestClient(app)


@pytest.fixture
def seeded(client):
    now = datetime.now(UTC)
    with session_scope() as session:
        session.add(
            Package(
                ecosystem="npm", name="express", latest_version="5.3.0",
                license="MIT", maintainers=5, deprecated=False,
                last_published_at=now - timedelta(days=20),
            )
        )
        session.add(
            Package(
                ecosystem="npm", name="abandoned-thing", latest_version="0.1.0",
                license=None, maintainers=1, deprecated=True,
                last_published_at=now - timedelta(days=1200),
            )
        )
        session.add(
            Advisory(
                ecosystem="npm", package_name="abandoned-thing",
                osv_id="GHSA-test-0001", severity="CRITICAL",
                summary="Remote code execution", fixed_version=None,
            )
        )
    return client


def test_healthz_reports_counts(seeded):
    body = seeded.get("/healthz").json()
    assert body["status"] == "ok"
    assert body["packages"] == 2
    assert body["advisories"] == 1


def test_healthz_works_on_an_empty_database(client):
    body = client.get("/healthz").json()
    assert body["status"] == "ok"
    assert body["packages"] == 0
    assert body["last_run"] is None


def test_analyse_scores_and_orders_by_risk(seeded):
    body = seeded.post(
        "/analyse", json={"names": ["express", "abandoned-thing"]}
    ).json()

    assert body["summary"]["analysed"] == 2
    # worst first
    assert body["analysed"][0]["name"] == "abandoned-thing"
    assert body["analysed"][0]["band"] == "high"
    assert body["analysed"][1]["name"] == "express"
    assert body["analysed"][1]["band"] == "low"


def test_analyse_explains_itself(seeded):
    body = seeded.post("/analyse", json={"names": ["abandoned-thing"]}).json()
    reasons = " ".join(body["analysed"][0]["reasons"])
    assert "critical severity advisory" in reasons
    assert "deprecated" in reasons
    assert "single maintainer" in reasons
    assert "no licence declared" in reasons


def test_analyse_accepts_a_manifest(seeded):
    manifest = {
        "dependencies": {"express": "^5.0.0"},
        "devDependencies": {"abandoned-thing": "^0.1.0"},
    }
    body = seeded.post("/analyse", json={"manifest": manifest}).json()
    assert body["summary"]["requested"] == 2
    assert {r["name"] for r in body["analysed"]} == {"express", "abandoned-thing"}


def test_unknown_packages_are_reported_not_invented(seeded):
    """The warehouse answers for what it has ingested; it does not guess."""
    body = seeded.post(
        "/analyse", json={"names": ["express", "never-ingested"]}
    ).json()
    assert body["unknown"] == ["never-ingested"]
    assert body["summary"]["unknown"] == 1
    assert {r["name"] for r in body["analysed"]} == {"express"}


def test_analyse_with_nothing_requested(seeded):
    body = seeded.post("/analyse", json={}).json()
    assert body["analysed"] == []
    assert body["summary"]["requested"] == 0


def test_packages_endpoint_lists_worst_first(seeded):
    body = seeded.get("/packages").json()
    assert body["count"] == 2
    assert body["packages"][0]["name"] == "abandoned-thing"
    assert body["packages"][0]["advisories"] == 1


def test_manifest_reader_covers_the_three_sections():
    names = names_from_manifest(
        {
            "dependencies": {"a": "1"},
            "devDependencies": {"b": "1"},
            "peerDependencies": {"c": "1"},
            "scripts": {"test": "pytest"},
        }
    )
    assert names == ["a", "b", "c"]


def test_manifest_reader_ignores_malformed_sections():
    assert names_from_manifest({"dependencies": "not-a-dict"}) == []
