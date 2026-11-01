"""Rate limiting and backoff.

Sleeps are stubbed, so these assert the retry *policy* without waiting.
"""

import httpx
import pytest

from depwatch.http import FetchError
from tests.conftest import mock_client


async def test_returns_json_on_success():
    client = mock_client(lambda request: httpx.Response(200, json={"ok": True}))
    assert await client.get_json("https://example.test/x") == {"ok": True}
    assert client.attempts == 1


async def test_retries_on_429_then_succeeds():
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        if calls["n"] < 3:
            return httpx.Response(429, headers={"Retry-After": "0"}, text="slow down")
        return httpx.Response(200, json={"ok": True})

    client = mock_client(handler)
    assert await client.get_json("https://example.test/x") == {"ok": True}
    assert calls["n"] == 3


async def test_retries_on_5xx():
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return httpx.Response(503) if calls["n"] == 1 else httpx.Response(200, json=[])

    client = mock_client(handler)
    assert await client.get_json("https://example.test/x") == []
    assert calls["n"] == 2


async def test_gives_up_after_max_retries():
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return httpx.Response(503)

    client = mock_client(handler, max_retries=2)
    with pytest.raises(FetchError) as exc:
        await client.get_json("https://example.test/x")
    assert exc.value.status == 503
    assert calls["n"] == 3  # the first attempt plus two retries


async def test_404_is_not_retried():
    """A missing package will not appear on the fourth ask."""
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return httpx.Response(404, text="not found")

    client = mock_client(handler)
    with pytest.raises(FetchError) as exc:
        await client.get_json("https://example.test/nope")
    assert exc.value.status == 404
    assert calls["n"] == 1


async def test_connection_errors_are_retried():
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        if calls["n"] == 1:
            raise httpx.ConnectError("boom", request=request)
        return httpx.Response(200, json={"ok": True})

    client = mock_client(handler)
    assert await client.get_json("https://example.test/x") == {"ok": True}
    assert calls["n"] == 2
