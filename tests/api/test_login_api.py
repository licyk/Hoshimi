"""The login surface the web UI drives: list stations, start one, poll it."""

from __future__ import annotations

import json
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest

from hoshimi.core.config import Settings
from hoshimi.core.context import Services
from hoshimi.core.login import LoginManager
from hoshimi.core.registry import Registry


def _cline_client() -> httpx.Client:
    """A station that answers the whole device dance without a browser."""

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/authorize/device"):
            return httpx.Response(200, json={"device_code": "dc", "user_code": "UC", "verification_uri": "https://web.workos.com", "expires_in": 900, "interval": 1})
        if path.endswith("/authenticate"):
            return httpx.Response(200, json={"access_token": "raw", "refresh_token": "wr"})
        return httpx.Response(200, json={"success": True, "data": {"accessToken": "workos:jwt", "refreshToken": "cr", "userInfo": {"clineUserId": "usr-1", "email": "a@example.com"}}})

    return httpx.Client(transport=httpx.MockTransport(handler))


@pytest.fixture
def login_client(tmp_path: Path) -> Iterator[Any]:
    from fastapi.testclient import TestClient

    from hoshimi.api.app import create_app

    services = Services(
        settings=Settings(host="127.0.0.1", port=0),
        registry=Registry({}),
        login=LoginManager(settings=Settings(), config_path=tmp_path / "config.json", client=_cline_client()),
    )
    with TestClient(create_app(services, serve_ui=False), base_url="http://localhost") as test_client:
        yield test_client


def _poll_until_done(client: Any, session_id: str, timeout: float = 10.0) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        view = client.get("/api/v1/login/poll", params={"session_id": session_id}).json()
        if view["status"] != "pending":
            return view
        time.sleep(0.02)
    raise AssertionError("the login session never finished")


def test_login_providers_lists_the_interactive_stations(login_client: Any) -> None:
    response = login_client.get("/api/v1/login/providers")
    assert response.status_code == 200
    assert "cline" in response.json()
    assert "zcode" in response.json()


def test_unconfigured_login_stations_are_visible_for_the_channels_ui(login_client: Any) -> None:
    response = login_client.get("/api/v1/providers")

    assert response.status_code == 200
    rows = {row["id"]: row for row in response.json()}

    # CodeArts has a provider class but is not `requires_authorization`; the
    # login capability itself must still make it visible from a fresh install.
    codearts = rows["codearts"]
    assert codearts["display_name"] == "CodeArts (华为云)"
    assert codearts["account_ids"] == []
    assert codearts["models"] == 0
    assert codearts["enabled"] is False
    assert codearts["requires_authorization"] is True

    # Codex has login before its model lane, and must remain visible too.
    codex = rows["codex"]
    assert codex["display_name"] == "Codex (ChatGPT OAuth)"
    assert codex["requires_authorization"] is True


def test_start_then_poll_lands_the_account(login_client: Any, tmp_path: Path) -> None:
    started = login_client.post("/api/v1/login/start", json={"provider": "cline"})
    assert started.status_code == 200
    session = started.json()
    assert session["provider"] == "cline"
    assert session["status"] == "pending"

    done = _poll_until_done(login_client, session["id"])
    assert done["status"] == "succeeded"
    assert done["login_url"] == "https://web.workos.com"
    assert done["account_id"].startswith("cline-")

    config = json.loads((tmp_path / "config.json").read_text(encoding="utf-8"))
    accounts = config["providers"]["cline"]["accounts"]
    assert len(accounts) == 1
    assert accounts[0]["access_token"] == "workos:jwt"


def test_start_with_an_unknown_station_is_a_bad_request(login_client: Any) -> None:
    response = login_client.post("/api/v1/login/start", json={"provider": "nope"})
    assert response.status_code == 400
    assert "nope" in response.json()["message"]


def test_poll_of_an_unknown_session_is_a_404(login_client: Any) -> None:
    response = login_client.get("/api/v1/login/poll", params={"session_id": "deadbeef"})
    assert response.status_code == 404
