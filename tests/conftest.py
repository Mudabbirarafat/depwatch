import os

# Must be set before anything imports the app: creating a TestClient runs the
# lifespan hook, and seeding reaches out to npm and OSV for real.
os.environ.setdefault("SEED_ON_START", "0")
# Tests bind their own throwaway database; startup must not create a schema in
# whatever DATABASE_URL happens to point at.
os.environ.setdefault("INIT_DB_ON_START", "0")

import httpx
import pytest

from depwatch.db import init_db
from depwatch.http import RateLimitedClient


@pytest.fixture
def db_url(tmp_path):
    """A throwaway SQLite database per test."""
    url = f"sqlite:///{tmp_path / 'test.db'}"
    init_db(url)
    return url


@pytest.fixture
def registry_doc():
    """A realistic registry document, including the awkward shapes."""
    return {
        "name": "express",
        "description": "Fast, unopinionated, minimalist web framework",
        "dist-tags": {"latest": "4.19.2"},
        "maintainers": [{"name": "dougwilson"}, {"name": "wesleytodd"}],
        "repository": {"type": "git", "url": "git+https://github.com/expressjs/express.git"},
        "license": "MIT",
        "time": {"modified": "2024-03-25T10:00:00.000Z", "4.19.2": "2024-03-25T09:00:00.000Z"},
        "versions": {"4.19.2": {"description": "Fast web framework", "license": "MIT"}},
    }


@pytest.fixture
def mock_client():
    """Build a RateLimitedClient backed by an httpx MockTransport.

    Exposed as a fixture rather than an importable helper: importing from
    conftest only works when the repository root happens to be on sys.path,
    which is true in a shell and false on a CI runner.

    Sleeps are stubbed so retry paths run instantly.
    """

    async def no_sleep(_seconds):
        return None

    def _build(handler, **kwargs) -> RateLimitedClient:
        return RateLimitedClient(
            client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
            sleep=no_sleep,
            **kwargs,
        )

    return _build
