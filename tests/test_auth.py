"""Who may change things.

Reading is public on purpose. Writing is not, and the case worth testing most
is the one where the secret is missing: that has to disable the endpoint, not
unprotect it.
"""

import pytest
from fastapi.testclient import TestClient

from depwatch import refresh as refresh_module
from depwatch.api import app
from depwatch.auth import HEADER
from depwatch.db import init_db

TOKEN = "a-test-token-not-a-real-one"


@pytest.fixture
def client(tmp_path):
    init_db(f"sqlite:///{tmp_path / 'auth.db'}")
    return TestClient(app)


@pytest.fixture(autouse=True)
def quiet_refresh(monkeypatch):
    """Keep the endpoint off the network, and reset the module-wide state."""
    calls: list = []

    async def fake_refresh(names=None):
        calls.append(names)
        return {"packages": {"succeeded": 1}, "advisories": {"advisories": 2}}

    monkeypatch.setattr(refresh_module, "refresh", fake_refresh)
    refresh_module.state.running = False
    refresh_module.state.last_summary = None
    refresh_module.state.last_error = None
    yield calls
    refresh_module.state.running = False


def test_refresh_is_disabled_when_no_token_is_configured(client, monkeypatch):
    """Fails closed. The convenient reading - no token set, so none required -
    is how a half-finished deploy becomes an open write endpoint."""
    monkeypatch.delenv("REFRESH_TOKEN", raising=False)

    response = client.post("/refresh", headers={HEADER: "anything"})
    assert response.status_code == 503
    assert "REFRESH_TOKEN" in response.json()["detail"]


def test_refresh_rejects_a_wrong_token(client, monkeypatch):
    monkeypatch.setenv("REFRESH_TOKEN", TOKEN)
    assert client.post("/refresh", headers={HEADER: "wrong"}).status_code == 401


def test_refresh_rejects_a_missing_token(client, monkeypatch):
    monkeypatch.setenv("REFRESH_TOKEN", TOKEN)
    assert client.post("/refresh").status_code == 401


def test_refresh_accepts_the_right_token(client, monkeypatch):
    monkeypatch.setenv("REFRESH_TOKEN", TOKEN)
    response = client.post("/refresh", headers={HEADER: TOKEN})
    assert response.status_code == 202
    assert response.json()["status"] == "started"


def test_a_second_refresh_is_declined_rather_than_queued(client, monkeypatch):
    """Overlapping crawls would double the rate-limit pressure to write the
    same rows twice."""
    monkeypatch.setenv("REFRESH_TOKEN", TOKEN)
    refresh_module.state.running = True

    response = client.post("/refresh", headers={HEADER: TOKEN})
    assert response.status_code == 409
    assert response.json()["status"] == "already_running"


def test_the_status_endpoint_is_protected_too(client, monkeypatch):
    """It reports what the last run did, which is not public information."""
    monkeypatch.setenv("REFRESH_TOKEN", TOKEN)
    assert client.get("/refresh/status").status_code == 401
    assert client.get("/refresh/status", headers={HEADER: TOKEN}).status_code == 200


def test_reading_stays_public(client, monkeypatch):
    """The dashboard is the point; it must not need a secret."""
    monkeypatch.setenv("REFRESH_TOKEN", TOKEN)
    assert client.get("/").status_code == 200
    assert client.get("/healthz").status_code == 200
    assert client.get("/packages").status_code == 200
    assert client.post("/analyse", json={"names": ["express"]}).status_code == 200


def test_start_declines_while_one_is_running():
    """The guard itself, without the HTTP layer around it."""
    refresh_module.state.running = True
    assert refresh_module.start() is None
