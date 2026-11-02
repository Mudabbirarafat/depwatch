"""Authentication for the endpoints that change things.

Reading stays public. The dashboard is the point of the thing, and an analysis
is a pure function of data that is already public anyway.

Writing is different. `/refresh` starts a crawl against two external APIs and
rewrites the warehouse, so an open one is both a way to spend someone else's
rate limit and a way to keep this instance permanently busy.

One shared secret rather than user accounts, because there is exactly one
caller: the scheduled workflow. Accounts would be ceremony around a cron job.
"""

import hmac
import logging
import os

from fastapi import HTTPException, Request

log = logging.getLogger("depwatch.auth")

HEADER = "X-Depwatch-Token"
ENV_VAR = "REFRESH_TOKEN"


def configured_token() -> str | None:
    token = os.environ.get(ENV_VAR, "").strip()
    return token or None


def require_token(request: Request) -> None:
    """Reject anything without the shared secret.

    Fails closed: an unset secret makes the endpoint unavailable rather than
    unprotected. The convenient reading - "no token configured, so no token
    required" - is how a half-finished deploy becomes an open write endpoint,
    and it fails in the direction where nobody notices.
    """
    expected = configured_token()
    if expected is None:
        log.error("refresh attempted with no token configured", extra={"header": HEADER})
        raise HTTPException(
            status_code=503,
            detail=f"This endpoint is disabled: {ENV_VAR} is not set on the server.",
        )

    supplied = request.headers.get(HEADER, "")
    # compare_digest rather than ==, so the comparison does not reveal how much
    # of a guess was correct through how long it took to reject it.
    if not hmac.compare_digest(supplied, expected):
        log.warning(
            "refresh rejected",
            extra={"supplied_a_token": bool(supplied), "client": request.client.host if request.client else None},
        )
        raise HTTPException(status_code=401, detail="Invalid or missing token.")
