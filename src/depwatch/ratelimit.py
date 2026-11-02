"""A per-client request limit.

This is in-process and per-instance, which is the honest shape of it: two
instances mean two independent buckets, and a restart forgets every count.
A shared limiter needs Redis, and Redis is not worth a dependency here.

It is still worth having. `/packages` scores every package in the ecosystem
before it can order them, so a client in a loop turns one open endpoint into
sustained full-table work. A limit that is merely approximate stops that; no
limit at all does not.

`/healthz` is exempt. The platform polls it continuously to decide whether the
instance is alive, and an instance that answers 429 to its own health check
looks dead.
"""

import math
import os
import time
from collections import deque
from collections.abc import Callable

# Enough for a person clicking around the dashboard, low enough to stop a loop.
DEFAULT_LIMIT_PER_MINUTE = 60

EXEMPT_PATHS = frozenset({"/healthz"})

# Bounds the memory one address range can cost us. Idle keys are dropped first.
MAX_TRACKED_CLIENTS = 10_000


class RateLimiter:
    """A sliding window of request timestamps per client.

    Sliding rather than fixed: a fixed window lets a client spend its whole
    allowance in the last second of one window and again in the first second of
    the next, which is twice the limit over two adjacent seconds.

    The clock is injected so the tests can advance time instead of sleeping.
    """

    def __init__(
        self,
        limit: int = DEFAULT_LIMIT_PER_MINUTE,
        window_seconds: float = 60.0,
        clock: Callable[[], float] = time.monotonic,
        max_clients: int = MAX_TRACKED_CLIENTS,
    ) -> None:
        self.limit = limit
        self.window_seconds = window_seconds
        self._clock = clock
        self._max_clients = max_clients
        self._hits: dict[str, deque[float]] = {}

    def check(self, key: str) -> tuple[bool, int]:
        """(allowed, seconds the client should wait before retrying)."""
        if self.limit <= 0:  # disabled
            return True, 0

        now = self._clock()
        cutoff = now - self.window_seconds
        hits = self._hits.setdefault(key, deque())
        while hits and hits[0] <= cutoff:
            hits.popleft()

        if len(hits) >= self.limit:
            # When the oldest hit leaves the window there is room again.
            return False, max(1, math.ceil(hits[0] + self.window_seconds - now))

        hits.append(now)
        self._forget_idle_clients(cutoff)
        return True, 0

    def _forget_idle_clients(self, cutoff: float) -> None:
        """Drop clients with nothing left in the window.

        Only runs once the table is already large: doing it on every request
        would make each request O(clients seen).
        """
        if len(self._hits) <= self._max_clients:
            return
        for key in [k for k, hits in self._hits.items() if not hits or hits[-1] <= cutoff]:
            del self._hits[key]


def client_key(forwarded_for: str | None, peer: str | None) -> str:
    """Identify the client behind a proxy.

    Render terminates TLS and proxies, so the peer address is the proxy for
    every request and X-Forwarded-For carries the original client as the first
    entry. The header is trusted only because the proxy overwrites it; reached
    directly, it would be spoofable and the limit sidesteppable per request.
    """
    if forwarded_for:
        first = forwarded_for.split(",")[0].strip()
        if first:
            return first
    return peer or "unknown"


_limiter: RateLimiter | None = None


def limiter() -> RateLimiter:
    """The process-wide limiter, built from the environment on first use."""
    global _limiter
    if _limiter is None:
        limit = int(os.environ.get("RATE_LIMIT_PER_MINUTE", DEFAULT_LIMIT_PER_MINUTE))
        _limiter = RateLimiter(limit=limit)
    return _limiter


def reset(replacement: RateLimiter | None = None) -> None:
    """Replace or clear the process-wide limiter. For tests and reconfiguration."""
    global _limiter
    _limiter = replacement
