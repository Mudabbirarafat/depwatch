"""Rate limiting and backoff.

Sleeps are stubbed, so these assert the retry *policy* without waiting.
"""

import logging

import httpx
import pytest

from depwatch.http import FetchError


async def test_returns_json_on_success(mock_client):
    client = mock_client(lambda request: httpx.Response(200, json={"ok": True}))
    assert await client.get_json("https://example.test/x") == {"ok": True}
    assert client.attempts == 1


async def test_retries_on_429_then_succeeds(mock_client):
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        if calls["n"] < 3:
            return httpx.Response(429, headers={"Retry-After": "0"}, text="slow down")
        return httpx.Response(200, json={"ok": True})

    client = mock_client(handler)
    assert await client.get_json("https://example.test/x") == {"ok": True}
    assert calls["n"] == 3


async def test_retries_on_5xx(mock_client):
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return httpx.Response(503) if calls["n"] == 1 else httpx.Response(200, json=[])

    client = mock_client(handler)
    assert await client.get_json("https://example.test/x") == []
    assert calls["n"] == 2


async def test_gives_up_after_max_retries(mock_client):
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return httpx.Response(503)

    client = mock_client(handler, max_retries=2)
    with pytest.raises(FetchError) as exc:
        await client.get_json("https://example.test/x")
    assert exc.value.status == 503
    assert calls["n"] == 3  # the first attempt plus two retries


async def test_404_is_not_retried(mock_client):
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


async def test_connection_errors_are_retried(mock_client):
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        if calls["n"] == 1:
            raise httpx.ConnectError("boom", request=request)
        return httpx.Response(200, json={"ok": True})

    client = mock_client(handler)
    assert await client.get_json("https://example.test/x") == {"ok": True}
    assert calls["n"] == 2


async def test_a_retry_is_logged_with_the_status_and_attempt(mock_client, caplog):
    """A retried request is invisible from the outside - it eventually
    succeeds - so the only record that a registry is throttling us is here."""
    caplog.set_level(logging.WARNING, logger="depwatch.http")
    seen = {"n": 0}

    def handler(request):
        seen["n"] += 1
        if seen["n"] == 1:
            return httpx.Response(429, headers={"Retry-After": "0"}, json={})
        return httpx.Response(200, json={"ok": True})

    await mock_client(handler).get_json("https://registry.example/x")

    retry = [r for r in caplog.records if r.message == "retrying request"]
    assert len(retry) == 1
    assert retry[0].status == 429
    assert retry[0].attempt == 1
    assert retry[0].retry_after_header == "0"


async def test_giving_up_is_logged_as_an_error(mock_client, caplog):
    caplog.set_level(logging.WARNING, logger="depwatch.http")

    def handler(request):
        return httpx.Response(503, json={})

    with pytest.raises(FetchError):
        await mock_client(handler, max_retries=1).get_json("https://registry.example/x")

    gave_up = [r for r in caplog.records if r.message == "giving up on request"]
    assert len(gave_up) == 1
    assert gave_up[0].status == 503
    assert gave_up[0].attempts == 2
