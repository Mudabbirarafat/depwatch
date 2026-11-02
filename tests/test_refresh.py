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
