"""What a refresh re-ingests, and what it does when it cannot."""

import pytest

from depwatch import refresh as refresh_module
from depwatch.db import init_db, session_scope
from depwatch.models import Package
from depwatch.refresh import known_names, refresh
from depwatch.seed import SEED_PACKAGES


@pytest.fixture
def db(tmp_path):
    init_db(f"sqlite:///{tmp_path / 'refresh.db'}")


@pytest.fixture(autouse=True)
def clean_state():
    refresh_module.state.running = False
    refresh_module.state.last_summary = None
    refresh_module.state.last_error = None
    yield
    refresh_module.state.running = False


def add(*names, ecosystem="npm"):
    with session_scope() as session:
        for name in names:
            session.add(
                Package(ecosystem=ecosystem, name=name, latest_version="1.0.0", deprecated=False)
            )


def test_known_names_are_what_gets_refreshed(db):
    """Going stale is a property of what you already have."""
    add("lodash", "express", "axios")
    assert known_names() == ["axios", "express", "lodash"]


def test_known_names_are_scoped_to_the_ecosystem(db):
    add("lodash")
    add("requests", ecosystem="pypi")
    assert known_names("npm") == ["lodash"]
    assert known_names("pypi") == ["requests"]


async def test_an_empty_warehouse_falls_back_to_the_seed_set(db, monkeypatch):
    """A cold instance that has not finished seeding still gets somewhere."""
    asked: list = []

    async def fake_npm(names, **kwargs):
        asked.append(names)
        return {"succeeded": len(names), "failed": 0}

    async def fake_osv(names, **kwargs):
        return {"advisories": 0, "failed": 0}

    monkeypatch.setattr(refresh_module, "ingest_npm", fake_npm)
    monkeypatch.setattr(refresh_module, "ingest_advisories", fake_osv)

    await refresh()
    assert asked == [SEED_PACKAGES]


async def test_a_failing_refresh_is_recorded_not_raised(db, monkeypatch):
    """It runs as a background task, so an exception that escaped would be an
    unretrieved error on the event loop rather than something anyone sees."""

    async def explode(names=None):
        raise RuntimeError("the registry said no")

    monkeypatch.setattr(refresh_module, "refresh", explode)
    await refresh_module._run(None)

    assert refresh_module.state.running is False
    assert "the registry said no" in refresh_module.state.last_error


async def test_a_successful_refresh_clears_the_previous_error(db, monkeypatch):
    refresh_module.state.last_error = "something from last time"

    async def fine(names=None):
        return {"packages": {"succeeded": 3}, "advisories": {"advisories": 7}}

    monkeypatch.setattr(refresh_module, "refresh", fine)
    await refresh_module._run(None)

    assert refresh_module.state.last_error is None
    assert refresh_module.state.last_summary["packages"]["succeeded"] == 3


def test_the_running_flag_is_cleared_even_after_a_failure(db, monkeypatch):
    """Otherwise one failed refresh declines every later one with 409."""

    async def explode(names=None):
        raise RuntimeError("boom")

    monkeypatch.setattr(refresh_module, "refresh", explode)
    import asyncio

    refresh_module.state.running = True
    asyncio.run(refresh_module._run(None))
    assert refresh_module.state.running is False


async def test_a_half_seeded_warehouse_is_seeded_again(db, monkeypatch):
    """Found in production, by the gate that asks whether any advisories were
    fetched at all.

    Seeding fetches npm first and OSV second. The instance was replaced between
    the two, so it came back with ten packages and no advisories - and because
    `seed_if_empty` checked the package count alone, it called that finished
    and left it that way. "Populated" has to mean both halves ran.
    """
    from depwatch import seed as seed_module

    add("lodash", "express")  # packages present, no advisories
    seeded: list = []

    async def fake_seed(names=None):
        seeded.append(names)
        return {}

    monkeypatch.setattr(seed_module, "seed", fake_seed)
    monkeypatch.setenv("SEED_ON_START", "1")
    monkeypatch.setenv("INIT_DB_ON_START", "0")

    await seed_module.seed_if_empty()
    assert seeded, "a warehouse with no advisories should have been seeded again"


async def test_a_fully_seeded_warehouse_is_left_alone(db, monkeypatch):
    from depwatch import seed as seed_module
    from depwatch.models import Advisory

    add("lodash")
    with session_scope() as session:
        session.add(
            Advisory(ecosystem="npm", package_name="lodash", osv_id="GHSA-x", severity="LOW")
        )

    seeded: list = []

    async def fake_seed(names=None):
        seeded.append(names)
        return {}

    monkeypatch.setattr(seed_module, "seed", fake_seed)
    monkeypatch.setenv("SEED_ON_START", "1")
    monkeypatch.setenv("INIT_DB_ON_START", "0")

    await seed_module.seed_if_empty()
    assert not seeded, "a populated warehouse must not be re-crawled on every boot"
