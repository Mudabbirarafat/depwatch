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


def mock_client(handler, **kwargs) -> RateLimitedClient:
    """A RateLimitedClient backed by an httpx MockTransport.

    Sleeps are replaced so retry paths run instantly.
    """

    async def no_sleep(_seconds):
        return None

    transport = httpx.MockTransport(handler)
    return RateLimitedClient(
        client=httpx.AsyncClient(transport=transport),
        sleep=no_sleep,
        **kwargs,
    )
