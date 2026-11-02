"""Drive a refresh on the deployed instance, then check the gates.

This is the pipeline's scheduler. Render's free plan has no cron jobs, so the
schedule lives in the workflow that runs this and the work happens over HTTP
against the live instance. The side effect is that the schedule is in the
repository and reviewable, rather than a setting somebody changed in a console.

Standard library only, so the job needs no install step.

Exit codes: 0 when the refresh ran and every gate passed, 1 otherwise. A
non-zero exit fails the workflow, and a failed scheduled workflow is what
GitHub emails about - which is the alerting.
"""

import json
import os
import sys
import time
import urllib.error
import urllib.request

TOKEN_HEADER = "X-Depwatch-Token"

def _seconds(name: str, default: int) -> int:
    try:
        return int(os.environ[name])
    except (KeyError, ValueError):
        return default


# A free instance sleeps when idle and takes the better part of a minute to
# wake, so the first request has to be allowed to be very slow. Overridable
# because the budget that suits a cold free instance is absurd for a test.
WAKE_TIMEOUT_SECONDS = _seconds("WAKE_TIMEOUT_SECONDS", 120)
REQUEST_TIMEOUT_SECONDS = _seconds("REQUEST_TIMEOUT_SECONDS", 90)
REFRESH_TIMEOUT_SECONDS = _seconds("REFRESH_TIMEOUT_SECONDS", 420)
POLL_SECONDS = _seconds("POLL_SECONDS", 10)


def fail(message: str) -> None:
    print(f"::error::{message}")
    sys.exit(1)


def request(url: str, method: str = "GET", token: str | None = None, timeout: int = REQUEST_TIMEOUT_SECONDS):
    """(status, parsed body). Never raises for an HTTP status."""
    req = urllib.request.Request(url, method=method)
    req.add_header("Accept", "application/json")
    req.add_header("User-Agent", "depwatch-scheduled-refresh")
    if token:
        req.add_header(TOKEN_HEADER, token)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            return response.status, json.loads(response.read() or b"{}")
    except urllib.error.HTTPError as exc:
        try:
            return exc.code, json.loads(exc.read() or b"{}")
        except json.JSONDecodeError:
            return exc.code, {}


def wake(base: str) -> None:
    """Keep knocking until the instance answers, or give up loudly."""
    deadline = time.monotonic() + WAKE_TIMEOUT_SECONDS
    attempt = 0
    while time.monotonic() < deadline:
        attempt += 1
        try:
            status, body = request(f"{base}/healthz")
        except Exception as exc:  # noqa: BLE001 - a sleeping instance throws all sorts
            print(f"  attempt {attempt}: not up yet ({type(exc).__name__})")
        else:
            if status == 200:
                print(f"  awake after {attempt} attempt(s): {body['packages']} packages")
                return
            print(f"  attempt {attempt}: HTTP {status}")
        time.sleep(POLL_SECONDS)
    fail(f"the instance did not answer /healthz within {WAKE_TIMEOUT_SECONDS}s")


def start_refresh(base: str, token: str) -> None:
    status, body = request(f"{base}/refresh", method="POST", token=token)
    if status == 202:
        print("  refresh started")
        return
    if status == 409:
        # Someone or something else is already doing it. Waiting on that run is
        # better than failing the job over a race with ourselves.
        print("  a refresh was already running; waiting for that one instead")
        return
    if status == 401:
        fail("the instance rejected the token - check the DEPWATCH_REFRESH_TOKEN secret")
    if status == 503:
        fail("the instance has no REFRESH_TOKEN configured, so /refresh is disabled")
    fail(f"unexpected response starting the refresh: HTTP {status} {body}")


def wait_for_refresh(base: str, token: str) -> None:
    deadline = time.monotonic() + REFRESH_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        status, body = request(f"{base}/refresh/status", token=token)
        if status != 200:
            fail(f"could not read the refresh status: HTTP {status}")
        if not body.get("running"):
            if body.get("last_error"):
                fail(f"the refresh failed: {body['last_error']}")
            print(f"  refresh finished: {json.dumps(body.get('last_summary'))}")
            return
        print("  still refreshing...")
        time.sleep(POLL_SECONDS)
    fail(f"the refresh did not finish within {REFRESH_TIMEOUT_SECONDS}s")


def check_gates(base: str) -> None:
    status, body = request(f"{base}/gates")
    for gate in body.get("gates", []):
        print(f"  {'PASS' if gate['passed'] else 'FAIL'}  {gate['name']:26} {gate['detail']}")
    if status != 200:
        fail(f"gates failed: {[g['name'] for g in body.get('gates', []) if not g['passed']]}")
    print("  all gates passed")


def main() -> int:
    base = os.environ.get("DEPWATCH_URL", "").rstrip("/")
    token = os.environ.get("DEPWATCH_REFRESH_TOKEN", "")
    if not base:
        fail("DEPWATCH_URL is not set (repository secret)")
    if not token:
        fail("DEPWATCH_REFRESH_TOKEN is not set (repository secret)")

    print("Waking the instance")
    wake(base)
    print("Starting the refresh")
    start_refresh(base, token)
    print("Waiting for it to finish")
    wait_for_refresh(base, token)
    print("Checking the gates")
    check_gates(base)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
