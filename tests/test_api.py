"""The API: /healthz, /analyse, /packages.

These run against a real (temporary) database rather than mocks, so they
exercise the queries as well as the handlers.
"""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from depwatch import api
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
                known_versions=["4.17.1", "4.18.0", "4.19.2", "4.20.0", "5.0.0", "5.3.0"],
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
        # Fixed long ago: harmless on 5.3.0, dangerous on a 4.17 pin.
        session.add(
            Advisory(
                ecosystem="npm", package_name="express",
                osv_id="GHSA-test-0002", severity="HIGH",
                summary="Open redirect", fixed_version="4.19.2",
            )
        )
    return client


def test_healthz_reports_counts(seeded):
    body = seeded.get("/healthz").json()
    assert body["status"] == "ok"
    assert body["packages"] == 2
    assert body["advisories"] == 2


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


def test_a_pinned_manifest_range_is_scored_at_that_version(seeded):
    """The understated-risk case: express 5.3.0 is clean, but a project pinned
    to ^4.17.1 resolves to 4.20.0 ... which is past the fix, so still clean."""
    body = seeded.post(
        "/analyse", json={"manifest": {"dependencies": {"express": "^4.17.1"}}}
    ).json()
    report = body["analysed"][0]
    assert report["requested_range"] == "^4.17.1"
    assert report["scored_version"] == "4.20.0"
    assert report["resolution"] == "resolved"


def test_an_old_pin_is_exposed_where_latest_is_not(seeded):
    """Same package, same advisory, different answer - which is the point."""
    at_latest = seeded.post("/analyse", json={"names": ["express"]}).json()["analysed"][0]
    pinned = seeded.post(
        "/analyse", json={"manifest": {"dependencies": {"express": "4.18.0"}}}
    ).json()["analysed"][0]

    assert at_latest["score"] == 0
    assert pinned["scored_version"] == "4.18.0"
    assert pinned["score"] > at_latest["score"]
    assert "high severity advisory" in " ".join(pinned["reasons"])


def test_unsupported_range_is_flagged_in_the_report(seeded):
    body = seeded.post(
        "/analyse", json={"manifest": {"dependencies": {"express": "^4 || ^5"}}}
    ).json()
    report = body["analysed"][0]
    assert report["resolution"] == "unsupported-range"
    assert "not supported" in " ".join(report["reasons"])
    assert body["summary"]["unresolved_ranges"] == 1


def test_range_matching_no_published_version_is_flagged(seeded):
    body = seeded.post(
        "/analyse", json={"manifest": {"dependencies": {"express": "^9.0.0"}}}
    ).json()
    report = body["analysed"][0]
    assert report["resolution"] == "no-matching-version"
    assert "no published version satisfies" in " ".join(report["reasons"])


def test_bare_names_are_scored_against_latest(seeded):
    report = seeded.post("/analyse", json={"names": ["express"]}).json()["analysed"][0]
    assert report["requested_range"] is None
    assert report["resolution"] == "latest"
    assert report["scored_version"] == "5.3.0"


def test_startup_creates_the_schema_before_the_port_opens(tmp_path, monkeypatch):
    """The deploy race, as a test.

    Render sends a health check as soon as the port is listening. /healthz
    counts rows, so on a fresh instance - where nothing has ever run `init-db`
    and seeding is still in flight - it has to find tables already there. If it
    500s instead, the platform reports a working deploy as a failed one.

    Using TestClient as a context manager is what runs the lifespan hook; the
    other tests here deliberately do not, so they keep their own database.
    """
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'fresh.db'}")
    monkeypatch.setenv("INIT_DB_ON_START", "1")
    monkeypatch.setenv("SEED_ON_START", "0")

    with TestClient(app) as fresh:
        body = fresh.get("/healthz").json()

    assert body["status"] == "ok"
    assert body["packages"] == 0
    assert body["last_run"] is None


def test_startup_can_be_told_not_to_touch_the_schema(tmp_path, monkeypatch):
    """Where migrations are run from outside, startup stays out of the way."""
    target = tmp_path / "untouched.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{target}")
    monkeypatch.setenv("INIT_DB_ON_START", "0")
    monkeypatch.setenv("SEED_ON_START", "0")

    with TestClient(app):
        pass

    assert not target.exists()


def test_the_dashboard_is_served_at_the_root(seeded):
    """The built React app, if it has been built.

    What the page *does* is tested in frontend/ with Vitest, against the
    components themselves. What matters here is that FastAPI hands the page
    over with the right content type and that it references the hashed asset
    bundle the build produced.
    """
    if not api.INDEX.is_file():
        pytest.skip("frontend not built; run `npm run build` in frontend/")

    page = seeded.get("/")
    assert page.status_code == 200
    assert "text/html" in page.headers["content-type"]
    assert "/assets/" in page.text


def test_a_missing_frontend_build_explains_itself(seeded, monkeypatch):
    """A page that was never built is a configuration state, not a fault.

    A 500 from a missing file sends someone looking for a server bug. This
    says which command to run, and still lists the API's own endpoints, so the
    service remains usable with no dashboard in front of it.
    """
    monkeypatch.setattr(api, "INDEX", Path("does-not-exist") / "index.html")

    response = seeded.get("/")
    assert response.status_code == 503
    body = response.json()
    assert "npm" in body["detail"]
    assert "POST /analyse" in body["api"].values()


def test_packages_orders_before_limiting(seeded):
    """The bug this replaced returned an arbitrary slice and called it the worst.

    `aaa-safe` sorts first by name and scores nothing; `abandoned-thing` is the
    risky one. Asking for a single row has to return the risky one, which only
    happens if the sort runs before the limit.
    """
    with session_scope() as session:
        session.add(
            Package(
                ecosystem="npm", name="aaa-safe", latest_version="2.0.0",
                license="MIT", maintainers=6, deprecated=False,
                last_published_at=datetime.now(UTC) - timedelta(days=3),
            )
        )

    body = seeded.get("/packages?limit=1").json()
    assert body["total"] == 3
    assert body["count"] == 1
    assert body["packages"][0]["name"] == "abandoned-thing"


def test_packages_only_counts_advisories_from_its_own_ecosystem(seeded):
    """Scoping the advisory query was the other half of the same bug."""
    with session_scope() as session:
        session.add(
            Package(
                ecosystem="pypi", name="express", latest_version="1.0.0",
                license="MIT", maintainers=3, deprecated=False,
                last_published_at=datetime.now(UTC) - timedelta(days=5),
            )
        )

    body = seeded.get("/packages?ecosystem=pypi").json()
    assert [p["name"] for p in body["packages"]] == ["express"]
    # The npm express carries an advisory under the same package name; it must
    # not be attributed to the PyPI package.
    assert body["packages"][0]["advisories"] == 0


def test_packages_rejects_an_absurd_page_size(seeded):
    assert seeded.get("/packages?limit=100000").status_code == 422
    assert seeded.get("/packages?limit=0").status_code == 422


def test_analyse_refuses_more_packages_than_it_will_score(seeded):
    """An open endpoint with no ceiling turns one request into one huge query."""
    body = seeded.post("/analyse", json={"names": [f"pkg-{i}" for i in range(201)]})
    assert body.status_code == 422


def test_analyse_counts_names_and_manifest_together(seeded):
    """Either half can be under the limit while the request is over it."""
    response = seeded.post(
        "/analyse",
        json={
            "names": [f"a-{i}" for i in range(150)],
            "manifest": {"dependencies": {f"b-{i}": "^1.0.0" for i in range(150)}},
        },
    )
    assert response.status_code == 422
    assert "the limit is 200" in response.text


def test_analyse_refuses_a_name_longer_than_the_column(seeded):
    response = seeded.post("/analyse", json={"names": ["x" * 256]})
    assert response.status_code == 422


def test_analyse_still_accepts_a_request_at_the_limit(seeded):
    """The cap must not reject the realistic case it exists to bound."""
    response = seeded.post("/analyse", json={"names": [f"pkg-{i}" for i in range(200)]})
    assert response.status_code == 200
    assert response.json()["summary"]["requested"] == 200
