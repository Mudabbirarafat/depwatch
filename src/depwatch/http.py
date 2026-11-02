"""HTTP client with rate limiting and backoff.

Public APIs will rate limit you, and a pipeline that falls over the first time
it sees a 429 is not a pipeline. This wraps httpx with:

* a concurrency bound, so one run cannot open fifty sockets at a registry;
* retries on 429 and 5xx with exponential backoff and jitter;
* honouring Retry-After when the server sends it, because guessing is rude.

4xx other than 429 are not retried - they will not get better.

GET and POST share one request path so the retry policy cannot drift between
them: npm is a GET API and OSV is a POST API, and both need the same care.
"""

import asyncio
import random
from typing import Any

import httpx

from depwatch.config import settings

RETRY_STATUS = {429, 500, 502, 503, 504}


class FetchError(RuntimeError):
    """Raised when a request could not be completed after retries."""

    def __init__(self, url: str, status: int | None, message: str):
        super().__init__(f"{url} -> {status}: {message}")
        self.url = url
        self.status = status


def _backoff_seconds(attempt: int, retry_after: str | None) -> float:
    if retry_after:
        try:
            return float(retry_after)
        except ValueError:
            pass
    # 0.5, 1, 2, 4 ... with jitter so parallel workers do not sync up
    return (0.5 * 2**attempt) + random.uniform(0, 0.3)


class RateLimitedClient:
    def __init__(
        self,
        client: httpx.AsyncClient | None = None,
        max_concurrency: int | None = None,
        max_retries: int | None = None,
        sleep=asyncio.sleep,
    ):
        self._client = client or httpx.AsyncClient(timeout=settings.request_timeout)
        self._sem = asyncio.Semaphore(max_concurrency or settings.max_concurrency)
        self._max_retries = max_retries if max_retries is not None else settings.max_retries
        self._sleep = sleep
        self.attempts = 0

    async def _request_json(self, method: str, url: str, json_body: Any | None = None) -> Any:
        last_status: int | None = None
        last_message = "no attempts made"

        for attempt in range(self._max_retries + 1):
            retry_after: str | None = None
            async with self._sem:
                self.attempts += 1
                try:
                    response = await self._client.request(method, url, json=json_body)
                except httpx.RequestError as exc:
                    last_status, last_message = None, str(exc)
                else:
                    if response.status_code < 400:
                        return response.json()
                    last_status = response.status_code
                    last_message = response.text[:200]
                    if response.status_code not in RETRY_STATUS:
                        raise FetchError(url, last_status, last_message)
                    retry_after = response.headers.get("Retry-After")

            if attempt < self._max_retries:
                await self._sleep(_backoff_seconds(attempt, retry_after))

        raise FetchError(url, last_status, last_message)

    async def get_json(self, url: str) -> Any:
        return await self._request_json("GET", url)

    async def post_json(self, url: str, payload: Any) -> Any:
        return await self._request_json("POST", url, json_body=payload)

    async def aclose(self) -> None:
        await self._client.aclose()
