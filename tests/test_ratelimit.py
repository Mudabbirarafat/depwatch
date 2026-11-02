"""The request limit.

Time is injected throughout, so these assert the window's behaviour rather
than waiting for it.
"""

import pytest
from fastapi.testclient import TestClient

from depwatch import ratelimit
from depwatch.api import app
from depwatch.db import init_db
from depwatch.ratelimit import RateLimiter, client_key


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture
def clock():
    return FakeClock()


def test_requests_up_to_the_limit_are_allowed(clock):
    limiter = RateLimiter(limit=3, clock=clock)
    assert [limiter.check("a")[0] for _ in range(3)] == [True, True, True]


def test_the_request_past_the_limit_is_refused(clock):
    limiter = RateLimiter(limit=3, clock=clock)
    for _ in range(3):
        limiter.check("a")

    allowed, retry_after = limiter.check("a")
    assert allowed is False
    assert retry_after == 60  # the whole window, since nothing has aged out


def test_clients_are_counted_separately(clock):
    limiter = RateLimiter(limit=1, clock=clock)
    assert limiter.check("a")[0] is True
    assert limiter.check("b")[0] is True
    assert limiter.check("a")[0] is False


def test_allowance_returns_as_the_window_passes(clock):
    limiter = RateLimiter(limit=2, clock=clock)
    limiter.check("a")
    limiter.check("a")
    assert limiter.check("a")[0] is False

    clock.advance(61)
    assert limiter.check("a")[0] is True


def test_the_window_slides_rather_than_resetting(clock):
    """A fixed window would let a client spend the full allowance at the end of
    one window and again at the start of the next - twice the limit across two
    adjacent seconds. A sliding window does not."""
    limiter = RateLimiter(limit=2, clock=clock)
    limiter.check("a")
    limiter.check("a")

    clock.advance(30)  # half a window later the earlier hits still count
    assert limiter.check("a")[0] is False

    clock.advance(31)  # now the first two have aged out
    assert limiter.check("a")[0] is True


def test_retry_after_counts_down_as_hits_age(clock):
    limiter = RateLimiter(limit=1, clock=clock)
    limiter.check("a")
    clock.advance(45)

    allowed, retry_after = limiter.check("a")
    assert allowed is False
    assert retry_after == 15


def test_a_limit_of_zero_disables_the_check(clock):
    limiter = RateLimiter(limit=0, clock=clock)
    assert all(limiter.check("a")[0] for _ in range(500))


def test_idle_clients_are_forgotten(clock):
    """Otherwise one address range per request grows the table without bound."""
    limiter = RateLimiter(limit=5, clock=clock, max_clients=10)
    for i in range(20):
        limiter.check(f"client-{i}")
    assert len(limiter._hits) == 20

    clock.advance(61)
    limiter.check("someone-new")
    assert len(limiter._hits) == 1


def test_client_key_prefers_the_forwarded_address():
    """Behind the platform's proxy the peer is always the proxy."""
    assert client_key("203.0.113.9, 10.0.0.1", "10.0.0.1") == "203.0.113.9"


def test_client_key_falls_back_to_the_peer():
    assert client_key(None, "198.51.100.4") == "198.51.100.4"
    assert client_key("", "198.51.100.4") == "198.51.100.4"
    assert client_key("  ", "198.51.100.4") == "198.51.100.4"
    assert client_key(None, None) == "unknown"


@pytest.fixture
def client(tmp_path):
    init_db(f"sqlite:///{tmp_path / 'rl.db'}")
    return TestClient(app)


def test_the_api_returns_429_with_a_retry_after_header(client, clock):
    ratelimit.reset(RateLimiter(limit=2, clock=clock))

    assert client.get("/packages").status_code == 200
    assert client.get("/packages").status_code == 200

    refused = client.get("/packages")
    assert refused.status_code == 429
    assert refused.headers["Retry-After"] == "60"


def test_healthz_is_never_rate_limited(client, clock):
    """The platform polls it to decide whether the instance is alive."""
    ratelimit.reset(RateLimiter(limit=1, clock=clock))
    client.get("/packages")  # spends the allowance

    assert client.get("/packages").status_code == 429
    for _ in range(20):
        assert client.get("/healthz").status_code == 200
