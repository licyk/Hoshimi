"""Interactive logins: each station's request shape, and the config write-back.

The flows are synchronous functions over one HTTP client, so a MockTransport
answers them and the assertions pin the exact requests — the device-code dance,
the PKCE challenge/verifier pair, the "not yet" codes a station uses instead of
an HTTP status. The local-callback stations (codex, gemini, trae, lobsterai, codearts)
run on a thread while the test plays the browser.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import threading
import time
from collections.abc import Callable
from typing import Any
from urllib.parse import parse_qs, quote, urlsplit

import httpx
import pytest

from hoshimi.core.config import Settings
from hoshimi.core.login import LoginError, LoginManager, login_providers
from hoshimi.core.login import buddy as buddy_flow
from hoshimi.core.login import cline as cline_flow
from hoshimi.core.login import codearts as codearts_flow
from hoshimi.core.login import codex as codex_flow
from hoshimi.core.login import gemini as gemini_flow
from hoshimi.core.login import lobsterai as lobsterai_flow
from hoshimi.core.login import loomy as loomy_flow
from hoshimi.core.login import minimax as minimax_flow
from hoshimi.core.login import qoder as qoder_flow
from hoshimi.core.login import raccoon as raccoon_flow
from hoshimi.core.login import trae as trae_flow
from hoshimi.core.login import zcode as zcode_flow

# Every flow sleeps between polls; the tests only care about the request shapes.
ALL_FLOWS = (buddy_flow, cline_flow, codearts_flow, codex_flow, gemini_flow, lobsterai_flow, loomy_flow, minimax_flow, qoder_flow, raccoon_flow, trae_flow, zcode_flow)


@pytest.fixture(autouse=True)
def no_poll_pause(monkeypatch: Any) -> None:
    # Callback-based flows (gemini, trae, lobsterai, codearts) have no poll loop.
    for module in ALL_FLOWS:
        if hasattr(module, "poll_pause"):
            monkeypatch.setattr(module, "poll_pause", lambda _seconds: None)


def mock_client(handler: Callable[[httpx.Request], httpx.Response]) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def form(request: httpx.Request) -> dict[str, str]:
    """The request's form body; the polling stations all speak form-encoded."""
    from urllib.parse import parse_qs

    parsed = parse_qs(request.content.decode("utf-8"))
    return {key: values[0] for key, values in parsed.items()}


def payload(request: httpx.Request) -> dict[str, Any]:
    """Whatever body the request carries — form-encoded or JSON — as a dict."""
    content_type = request.headers.get("content-type", "")
    if "application/json" in content_type:
        try:
            body = json.loads(request.content)
        except ValueError:
            return {}
        return body if isinstance(body, dict) else {}
    return dict(form(request))


def run_flow(flow: Callable[[httpx.Client, Callable[[str], None]], dict[str, Any]], client: httpx.Client) -> tuple[dict[str, Any], list[str]]:
    """Run a blocking flow on a thread, capturing the URLs it reports."""
    reported: list[str] = []
    seen = threading.Event()
    result: dict[str, Any] = {}
    error: list[BaseException] = []

    def report(url: str) -> None:
        reported.append(url)
        seen.set()

    def target() -> None:
        try:
            result.update(flow(client, report))
        except BaseException as exc:  # noqa: BLE001 - the test asserts on it
            error.append(exc)
            seen.set()

    thread = threading.Thread(target=target, daemon=True)
    thread.start()
    assert seen.wait(10), "the flow never reported a login URL"
    result["_thread"] = thread
    result["_error"] = error
    result["_reported"] = reported
    return result, reported


def finish(flow_thread: threading.Thread, timeout: float = 10.0) -> None:
    flow_thread.join(timeout)
    assert not flow_thread.is_alive(), "the flow did not finish"


def callback_hit(login_url: str, path: str, **params: str) -> None:
    """Play the browser: hit the flow's own loopback callback with the answer."""
    parts = urlsplit(login_url)
    query = parse_qs(parts.query)
    # The redirect URI lives on the query of the login URL for most stations.
    redirect = query.get("redirect_uri", [""])[0]
    if redirect:
        target = urlsplit(redirect)
        port = target.port
    else:
        # TRAE puts its callback on the query too, under another name.
        redirect = query.get("auth_callback_url", [""])[0]
        target = urlsplit(redirect)
        port = target.port
    assert port, f"no callback port in {login_url}"
    query_text = "&".join(f"{key}={quote(value, safe='')}" for key, value in params.items())
    httpx.get(f"http://127.0.0.1:{port}{path}?{query_text}", timeout=10.0)


# -- cline ------------------------------------------------------------------ #
def test_cline_device_code_then_register() -> None:
    calls: list[tuple[str, dict[str, str]]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        host = request.url.host
        path = request.url.path
        body = payload(request)
        calls.append((f"{host}{path}", body))
        if host == "api.workos.com" and path.endswith("/authorize/device"):
            return httpx.Response(200, json={"device_code": "dc", "user_code": "UC", "verification_uri": "https://web.workos.com", "verification_uri_complete": "https://web.workos.com/?code=UC", "expires_in": 900, "interval": 1})
        if host == "api.workos.com" and path.endswith("/authenticate"):
            if len([c for c in calls if c[0].endswith("/authenticate")]) == 1:
                return httpx.Response(400, json={"error": "authorization_pending"})
            return httpx.Response(200, json={"access_token": "workos-raw", "refresh_token": "wr"})
        if host == "api.cline.bot":
            return httpx.Response(200, json={"success": True, "data": {"accessToken": "workos:jwt", "refreshToken": "cr", "expiresAt": "2027-01-02T03:04:05Z", "userInfo": {"clineUserId": "usr-1", "email": "a@example.com"}}})
        return httpx.Response(500)

    credential, reported = run_flow(cline_flow.start_cline_login, mock_client(handler))
    assert reported == ["https://web.workos.com/?code=UC"]
    # The flow goes on polling after it reports the URL: wait for it, or this reads a half-filled result.
    finish(credential["_thread"])
    assert credential["access_token"] == "workos:jwt"
    assert credential["account_id"] == "usr-1"
    assert credential["nickname"] == "a@example.com"
    assert isinstance(credential["expire_time"], int) and credential["expire_time"] > 1e12

    device = next(body for name, body in calls if name.endswith("/authorize/device"))
    assert device["client_id"] == "client_01K3A541FN8TA3EPPHTD2325AR"
    authenticate = next(body for name, body in calls if name.endswith("/authenticate"))
    assert authenticate["grant_type"] == "urn:ietf:params:oauth:grant-type:device_code"
    register = next(body for name, body in calls if name.endswith("/register"))
    assert register == {"accessToken": "workos-raw", "refreshToken": "wr"}


def test_cline_denial_is_reported_not_retried() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/authorize/device"):
            return httpx.Response(200, json={"device_code": "dc", "user_code": "UC", "verification_uri": "https://web.workos.com", "expires_in": 900, "interval": 1})
        return httpx.Response(400, json={"error": "access_denied"})

    result, _ = run_flow(cline_flow.start_cline_login, mock_client(handler))
    assert isinstance(result["_error"][0], LoginError)
    assert "access_denied" in str(result["_error"][0])


# -- minimax ---------------------------------------------------------------- #
def test_minimax_pkce_challenge_matches_the_verifier() -> None:
    import hashlib

    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/oauth2/device/code"):
            seen["device"] = payload(request)
            return httpx.Response(200, json={"device_code": "dc", "user_code": "UC", "verification_uri": "https://account.minimax.cn/v", "verification_uri_complete": "https://account.minimax.cn/v?c=UC", "expires_in": 900, "interval": 1})
        seen["token"] = payload(request)
        polls = seen.setdefault("polls", 0)
        seen["polls"] = polls + 1
        if polls == 0:
            return httpx.Response(200, json={"status": "pending"})
        return httpx.Response(200, json={"access_token": "ma", "refresh_token": "mr", "token_type": "Bearer", "expires_in": 3600, "scope": "agent.default"})

    credential, reported = run_flow(minimax_flow.start_minimax_login, mock_client(handler))
    assert reported == ["https://account.minimax.cn/v?c=UC"]
    assert credential["access_token"] == "ma"
    assert credential["token_type"] == "Bearer"
    assert int(credential["expires_at"]) > time.time() * 1000

    challenge = seen["device"]["code_challenge"]
    verifier = seen["token"]["code_verifier"]
    assert base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=") == challenge
    assert seen["device"]["scope"] == "agent.default"
    assert seen["device"]["audience"] == "agent-backend"


def test_minimax_refuses_a_token_without_the_agent_scope() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/oauth2/device/code"):
            return httpx.Response(200, json={"device_code": "dc", "user_code": "UC", "verification_uri": "https://account.minimax.cn/v", "expires_in": 900, "interval": 1})
        return httpx.Response(200, json={"access_token": "ma", "refresh_token": "mr", "token_type": "Bearer", "expires_in": 3600, "scope": "other"})

    result, _ = run_flow(minimax_flow.start_minimax_login, mock_client(handler))
    assert isinstance(result["_error"][0], LoginError)
    assert "agent.default" in str(result["_error"][0])


# -- buddy / workbuddy ------------------------------------------------------ #
def _jwt(claims: dict[str, Any]) -> str:
    def part(payload: dict[str, Any]) -> str:
        raw = json.dumps(payload).encode()
        return base64.urlsafe_b64encode(raw).decode().rstrip("=")

    # A JWT is header.payload.signature — the claims ride the middle part.
    return f"{part({'alg': 'none'})}.{part(claims)}.sig"


def test_buddy_state_poll_then_account() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/v2/plugin/auth/state"):
            seen["state_query"] = dict(request.url.params)
            seen["state_headers"] = dict(request.headers)
            return httpx.Response(200, json={"code": 0, "data": {"state": "s1", "authUrl": "https://www.codebuddy.cn/login/?platform=ide"}})
        if path.endswith("/v2/plugin/auth/token"):
            polls = seen.setdefault("token_polls", 0)
            seen["token_polls"] = polls + 1
            if polls == 0:
                return httpx.Response(200, json={"code": 11217, "message": "not ready"})
            return httpx.Response(200, json={"code": 0, "data": {"accessToken": _jwt({"sub": "u-1", "nickname": "Neo"}), "refreshToken": "br", "expiresIn": 3600, "tokenType": "Bearer", "domain": "copilot.tencent.com"}})
        if path.endswith("/v2/plugin/login/account"):
            polls = seen.setdefault("account_polls", 0)
            seen["account_polls"] = polls + 1
            seen["account_headers"] = dict(request.headers)
            if polls == 0:
                return httpx.Response(200, json={"code": 12151, "message": "not ready"})
            return httpx.Response(200, json={"code": 0, "data": {"uid": "u-1", "nickname": "", "enterpriseId": "ent-1", "type": "personal"}})
        return httpx.Response(500)

    credential, reported = run_flow(lambda client, report: buddy_flow.start_buddy_login(client, report, "buddy"), mock_client(handler))
    assert reported == ["https://www.codebuddy.cn/login/?platform=ide"]
    assert credential["user_id"] == "u-1"
    assert credential["nickname"] == "Neo"  # the account answer was empty; the JWT had it
    assert credential["enterprise_id"] == "ent-1"
    assert seen["state_query"]["platform"] == "ide"
    assert seen["account_headers"]["x-domain"] == "copilot.tencent.com"
    assert int(credential["expires_at"]) > time.time() * 1000


def test_workbuddy_decorates_the_link_and_uses_its_own_identity() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/v2/plugin/auth/state"):
            seen["query"] = dict(request.url.params)
            seen["ua"] = request.headers.get("user-agent", "")
            return httpx.Response(200, json={"code": 0, "data": {"state": "s1", "authUrl": "https://www.workbuddy.ai/login/?platform=workbuddy-ai"}})
        if path.endswith("/v2/plugin/auth/token"):
            return httpx.Response(200, json={"code": 0, "data": {"accessToken": _jwt({"sub": "u-2"}), "refreshToken": "br", "expiresIn": 60}})
        return httpx.Response(200, json={"code": 0, "data": {"uid": "u-2", "nickname": "", "enterpriseId": "", "type": "personal"}})

    _, reported = run_flow(lambda client, report: buddy_flow.start_buddy_login(client, report, "workbuddy"), mock_client(handler))
    url = reported[0]
    assert url.startswith("https://www.workbuddy.ai/login/")
    assert "version=5.5.2" in url
    assert "loginSessionId=" in url
    assert seen["query"]["platform"] == "workbuddy-ai"
    assert seen["ua"].startswith("WorkBuddy/")


# -- zcode ------------------------------------------------------------------ #
def test_zcode_init_then_ready() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/oauth/cli/init"):
            seen["init_headers"] = dict(request.headers)
            seen["init_body"] = json.loads(request.content)
            return httpx.Response(200, json={"code": 0, "data": {"flow_id": "f1", "authorize_url": "https://bigmodel.cn/login?x=1", "poll_interval_sec": 1, "expires_at": 0}})
        polls = seen.setdefault("polls", 0)
        seen["polls"] = polls + 1
        if polls == 0:
            return httpx.Response(200, json={"code": 0, "data": {"status": "pending"}})
        return httpx.Response(200, json={"code": 0, "data": {"status": "ready", "token": "zjwt", "user": {"user_id": "zu-1", "name": "Neo"}, "bigmodel": {"access_token": "bm", "refresh_token": "bmr"}}})

    credential, reported = run_flow(zcode_flow.start_zcode_login, mock_client(handler))
    assert reported == ["https://bigmodel.cn/login?x=1"]
    assert credential["zcode_jwt"] == "zjwt"
    assert credential["user_id"] == "zu-1"
    assert credential["bigmodel_access_token"] == "bm"
    assert len(credential["device_mid"]) == 32
    assert seen["init_body"] == {"provider": "bigmodel"}
    # The Bearer is the flow's own generated secret, not any user credential.
    assert seen["init_headers"]["authorization"].startswith("Bearer ")
    assert len(seen["init_headers"]["authorization"].split()[1]) == 64


def test_zcode_ready_without_the_third_party_token_still_succeeds() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/oauth/cli/init"):
            return httpx.Response(200, json={"code": 0, "data": {"flow_id": "f1", "authorize_url": "https://bigmodel.cn/login", "poll_interval_sec": 1}})
        return httpx.Response(200, json={"code": 0, "data": {"status": "ready", "token": "zjwt", "user": {"user_id": "zu-1"}}})

    credential, _ = run_flow(zcode_flow.start_zcode_login, mock_client(handler))
    assert credential["zcode_jwt"] == "zjwt"
    assert "bigmodel_access_token" not in credential


# -- qoder / qodercn -------------------------------------------------------- #
def test_qoder_builds_the_link_locally_and_polls_the_open_api_host() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["poll_host"] = request.url.host
        polls = seen.setdefault("polls", 0)
        seen["polls"] = polls + 1
        if polls == 0:
            return httpx.Response(404, json={"errorCode": "NotFound"})
        return httpx.Response(200, json={"token": "qt", "refresh_token": "qr", "expires_at": int((time.time() + 3600) * 1000), "refresh_token_expires_at": int((time.time() + 86400) * 1000), "user_id": "qu-1", "user_name": "Neo"})

    credential, reported = run_flow(lambda client, report: qoder_flow.start_qoder_login(client, report, "qoder"), mock_client(handler))
    link = reported[0]
    assert link.startswith("https://qoder.com/device/selectAccounts?")
    link_query = parse_qs(urlsplit(link).query)
    assert link_query["client_id"] == ["e883ade2-e6e3-4d6d-adf7-f92ceff5fdcb"]
    assert link_query["challenge_method"] == ["S256"]
    assert len(link_query["machine_id"][0]) == 36
    assert seen["poll_host"] == "openapi.qoder.sh"

    finish(credential["_thread"])
    assert credential["security_oauth_token"] == "qt"
    assert credential["access_token"] == "qt"
    assert credential["machine_id"] == link_query["machine_id"][0]
    assert credential["uid"] == "qu-1"
    assert credential["nickname"] == "Neo"


def test_qodercn_uses_the_cn_hosts() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["poll_host"] = request.url.host
        return httpx.Response(200, json={"token": "qt"})

    _, reported = run_flow(lambda client, report: qoder_flow.start_qoder_login(client, report, "qodercn"), mock_client(handler))
    assert reported[0].startswith("https://qoder.cn/device/selectAccounts?")
    assert parse_qs(urlsplit(reported[0]).query)["client_id"] == ["732aef47-9cf2-46a2-95fe-4cebb5d0d1fa"]
    assert seen["poll_host"] == "openapi.qoder.com.cn"


# -- gemini ----------------------------------------------------------------- #
def test_gemini_loopback_callback_then_token_exchange() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "oauth2.googleapis.com"
        seen["token_body"] = payload(request)
        sub_jwt = _jwt({"sub": "g-1", "email": "me@example.com"})
        return httpx.Response(200, json={"access_token": "ga", "refresh_token": "gr", "expires_in": 3600, "token_type": "Bearer", "scope": "openid cloud-platform", "id_token": sub_jwt})

    flow = gemini_flow.start_gemini_login
    captured: dict[str, Any] = {}
    error: list[BaseException] = []
    seen_url = threading.Event()

    def report(url: str) -> None:
        captured["url"] = url
        seen_url.set()

    def target() -> None:
        try:
            captured.update(flow(mock_client(handler), report))
        except BaseException as exc:  # noqa: BLE001
            error.append(exc)
            seen_url.set()

    thread = threading.Thread(target=target, daemon=True)
    thread.start()
    assert seen_url.wait(10)

    login_url = captured["url"]
    assert login_url.startswith("https://accounts.google.com/o/oauth2/v2/auth?")
    query = parse_qs(urlsplit(login_url).query)
    assert query["response_type"] == ["code"]
    assert query["access_type"] == ["offline"]
    assert query["prompt"] == ["consent"]
    redirect = query["redirect_uri"][0]
    assert redirect.startswith("http://localhost:")
    assert redirect.endswith(gemini_flow.CALLBACK_PATH)

    # The flow is still waiting for the browser; play it now.
    port = urlsplit(redirect).port
    state = query["state"][0]
    httpx.get(f"http://127.0.0.1:{port}/oauth-callback?code=the-code&state={state}", timeout=10.0)
    finish(thread)

    assert captured["access_token"] == "ga"
    assert captured["refresh_token"] == "gr"
    assert captured["sub"] == "g-1"
    assert captured["email"] == "me@example.com"
    assert seen["token_body"]["code"] == "the-code"
    assert seen["token_body"]["grant_type"] == "authorization_code"



# -- codex ------------------------------------------------------------------- #
def test_codex_loopback_pkce_then_token_exchange() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "auth.openai.com"
        assert request.url.path == "/oauth/token"
        seen["token_body"] = payload(request)
        id_token = _jwt(
            {
                "sub": "openai-sub",
                "email": "me@example.com",
                "exp": int(time.time()) + 3600,
                "https://api.openai.com/auth": {"chatgpt_account_id": "acct-1"},
            }
        )
        return httpx.Response(
            200,
            json={
                "access_token": "oa",
                "refresh_token": "or",
                "expires_in": 3600,
                "scope": "openid email profile offline_access",
                "id_token": id_token,
            },
        )

    captured: dict[str, Any] = {}
    error: list[BaseException] = []
    seen_url = threading.Event()

    def report(url: str) -> None:
        captured["url"] = url
        seen_url.set()

    def target() -> None:
        try:
            captured.update(codex_flow.start_codex_login(mock_client(handler), report))
        except BaseException as exc:  # noqa: BLE001
            error.append(exc)
            seen_url.set()

    thread = threading.Thread(target=target, daemon=True)
    thread.start()
    assert seen_url.wait(10)

    login_url = captured["url"]
    assert login_url.startswith("https://auth.openai.com/oauth/authorize?")
    query = parse_qs(urlsplit(login_url).query)
    assert query["client_id"] == [codex_flow.CLIENT_ID]
    assert query["redirect_uri"] == ["http://localhost:1455/auth/callback"]
    assert query["response_type"] == ["code"]
    assert query["scope"] == ["openid email profile offline_access"]
    assert query["audience"] == ["https://api.openai.com/v1"]
    assert query["code_challenge_method"] == ["S256"]
    assert query["state"]

    state = query["state"][0]
    httpx.get(f"http://127.0.0.1:1455/auth/callback?code=the-code&state={state}", timeout=10.0)
    finish(thread)
    assert not error, error

    assert captured["access_token"] == "oa"
    assert captured["refresh_token"] == "or"
    assert captured["account_id"] == "acct-1"
    assert captured["email"] == "me@example.com"
    assert int(captured["expires_at"]) > time.time() * 1000
    assert seen["token_body"]["grant_type"] == "authorization_code"
    assert seen["token_body"]["code"] == "the-code"
    assert seen["token_body"]["redirect_uri"] == "http://localhost:1455/auth/callback"

    challenge = query["code_challenge"][0]
    verifier = seen["token_body"]["code_verifier"]
    assert base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=") == challenge


def test_codex_requires_a_refresh_token_for_long_lived_login() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"access_token": "oa", "expires_in": 3600})

    result, reported = run_flow(codex_flow.start_codex_login, mock_client(handler))
    assert reported
    state = parse_qs(urlsplit(reported[0]).query)["state"][0]
    callback_hit(reported[0], codex_flow.CALLBACK_PATH, code="the-code", state=state)
    finish(result["_thread"])
    assert isinstance(result["_error"][0], LoginError)
    assert "no refresh token" in str(result["_error"][0])


def test_codex_rejects_a_callback_from_another_login() -> None:
    captured: dict[str, Any] = {}
    error: list[BaseException] = []
    seen_url = threading.Event()

    def report(url: str) -> None:
        captured["url"] = url
        seen_url.set()

    def target() -> None:
        try:
            codex_flow.start_codex_login(mock_client(lambda request: httpx.Response(500)), report)
        except BaseException as exc:  # noqa: BLE001
            error.append(exc)
            seen_url.set()

    thread = threading.Thread(target=target, daemon=True)
    thread.start()
    assert seen_url.wait(10)
    assert captured["url"]

    httpx.get("http://127.0.0.1:1455/auth/callback?code=the-code&state=wrong", timeout=10.0)
    finish(thread)
    assert len(error) == 1
    assert isinstance(error[0], LoginError)
    assert "state mismatch" in str(error[0])


def test_codex_rejects_a_second_concurrent_login() -> None:
    acquired = codex_flow._LOGIN_LOCK.acquire(blocking=False)
    assert acquired
    try:
        result, reported = run_flow(codex_flow.start_codex_login, mock_client(lambda request: httpx.Response(500)))
        assert reported == []
        assert isinstance(result["_error"][0], LoginError)
        assert "another login" in str(result["_error"][0])
    finally:
        codex_flow._LOGIN_LOCK.release()

# -- trae ------------------------------------------------------------------- #
def test_trae_callback_carries_tokens_and_the_exchange_rotates_them() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/ExchangeToken"):
            seen["exchange_body"] = json.loads(request.content)
            return httpx.Response(200, json={"Result": {"Token": "at2", "RefreshToken": "r2", "TokenExpireAt": 1786847930141}})
        if path.endswith("/GetUserInfo"):
            seen["userinfo_headers"] = dict(request.headers)
            return httpx.Response(200, json={"Result": {"UserID": "tu-1", "ScreenName": "Neo", "EnterpriseID": "ent", "NonPlainTextMobile": "130******00", "NonPlainTextEmail": ""}})
        return httpx.Response(500)

    flow = trae_flow.start_trae_login
    captured: dict[str, Any] = {}
    error: list[BaseException] = []
    seen_url = threading.Event()

    def report(url: str) -> None:
        captured["url"] = url
        seen_url.set()

    def target() -> None:
        try:
            captured.update(flow(mock_client(handler), report))
        except BaseException as exc:  # noqa: BLE001
            error.append(exc)
            seen_url.set()

    thread = threading.Thread(target=target, daemon=True)
    thread.start()
    assert seen_url.wait(10)

    login_url = captured["url"]
    assert login_url.startswith("https://www.trae.cn/authorization?")
    query = parse_qs(urlsplit(login_url).query)
    # The callback parameter is `auth_callback_url`; a `callback_url` gets ignored.
    assert query["auth_callback_url"][0].startswith("http://127.0.0.1:")
    assert query["client_id"] == [trae_flow.CLIENT_ID]
    assert len(query["machine_id"][0]) == 32
    assert len(query["device_id"][0]) == 32

    callback = query["auth_callback_url"][0]
    user_info = quote(json.dumps({"UserID": "tu-1", "ScreenName": "Neo", "TenantID": "ent"}))
    user_jwt = quote(json.dumps({"Token": "old", "RefreshToken": "r1"}))
    httpx.get(f"{callback}?refreshToken=r1&userInfo={user_info}&userJwt={user_jwt}", timeout=10.0)
    finish(thread)
    assert not error, error

    assert captured["access_token"] == "at2"
    assert captured["refresh_token"] == "r2"  # rotated by the exchange
    assert captured["uid"] == "tu-1"
    assert captured["nickname"] == "Neo"
    assert captured["phone"] == "130******00"
    assert captured["machine_id"] == query["machine_id"][0]
    assert captured["expires_at"] == "1786847930141"
    assert seen["exchange_body"]["RefreshToken"] == "r1"
    assert seen["userinfo_headers"]["x-cloudide-token"] == "at2"


# -- lobsterai -------------------------------------------------------------- #
def test_lobsterai_callback_then_exchange() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/auth/exchange"
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"code": 0, "data": {"accessToken": "la", "refreshToken": "lr", "expiresIn": 3600, "user": {"id": "lu-1", "userId": "LU-1", "nickname": "Neo"}}})

    flow = lobsterai_flow.start_lobsterai_login
    captured: dict[str, Any] = {}
    error: list[BaseException] = []
    seen_url = threading.Event()

    def report(url: str) -> None:
        captured["url"] = url
        seen_url.set()

    def target() -> None:
        try:
            captured.update(flow(mock_client(handler), report))
        except BaseException as exc:  # noqa: BLE001
            error.append(exc)
            seen_url.set()

    thread = threading.Thread(target=target, daemon=True)
    thread.start()
    assert seen_url.wait(10)

    login_url = captured["url"]
    assert login_url.startswith("https://lobsterai.youdao.com/portal#/login?")
    # The portal link carries its query inside the fragment.
    fragment_query = parse_qs(urlsplit(login_url).fragment.split("?", 1)[1])
    assert fragment_query["source"] == ["electron"]
    redirect = fragment_query["redirect_uri"][0]
    assert redirect.endswith(lobsterai_flow.CALLBACK_PATH)
    state = fragment_query["state"][0]

    port = urlsplit(redirect).port
    httpx.get(f"http://127.0.0.1:{port}/auth/callback?code=the-code&state={state}", timeout=10.0)
    finish(thread)
    assert not error, error

    assert captured["access_token"] == "la"
    assert captured["refresh_token"] == "lr"
    assert captured["uid"] == "lu-1"
    assert captured["user_id"] == "LU-1"
    assert len(captured["uuid"]) == 32
    assert seen["body"]["authCode"] == "the-code"
    assert seen["body"]["uuid"] == captured["uuid"]
    assert seen["body"]["version"] == lobsterai_flow.CLIENT_VERSION


def test_lobsterai_rejects_a_state_mismatch() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"code": 0, "data": {"accessToken": "la"}})

    flow = lobsterai_flow.start_lobsterai_login
    captured: dict[str, Any] = {}
    error: list[BaseException] = []
    seen_url = threading.Event()

    def report(url: str) -> None:
        captured["url"] = url
        seen_url.set()

    def target() -> None:
        try:
            captured.update(flow(mock_client(handler), report))
        except BaseException as exc:  # noqa: BLE001
            error.append(exc)
            seen_url.set()

    thread = threading.Thread(target=target, daemon=True)
    thread.start()
    assert seen_url.wait(10)
    redirect = parse_qs(urlsplit(captured["url"]).fragment.split("?", 1)[1])["redirect_uri"][0]
    port = urlsplit(redirect).port
    httpx.get(f"http://127.0.0.1:{port}/auth/callback?code=the-code&state=someone-elses", timeout=10.0)
    finish(thread)
    assert isinstance(error[0], LoginError)
    assert "state mismatch" in str(error[0])


# -- loomy / raccoon --------------------------------------------------------- #
def test_loomy_wechat_page_then_bound_account_login() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/connect/qrconnect":
            seen["auth_query"] = dict(request.url.params)
            return httpx.Response(200, text='<img class="js_qrcode_img" src="/connect/qrcode/loomy-uuid"/>')
        if path == "/connect/qrcode/loomy-uuid":
            return httpx.Response(200, content=b"\xff\xd8\xff" + b"q" * 240, headers={"Content-Type": "image/jpeg"})
        if path == "/connect/l/qrconnect":
            seen["poll_query"] = dict(request.url.params)
            return httpx.Response(200, text="wx_errcode=405; wx_code='wechat-code';")
        if path == "/login/thirdAccount/bind/auth":
            seen["auth_body"] = payload(request)
            seen["auth_headers"] = dict(request.headers)
            seen["auth_content"] = request.content.decode("utf-8")
            return httpx.Response(200, json={"code": "000000", "data": {"rcode": "rc", "bind": 1, "nickname": "WeChatUser"}})
        if path == "/login/thirdAccount/bind/skip":
            seen["skip_body"] = payload(request)
            return httpx.Response(200, json={"code": "000000", "data": {"session": "loomy-session", "userid": "loomy-user"}})
        return httpx.Response(500)

    result, reported = run_flow(loomy_flow.start_loomy_login, mock_client(handler))
    assert reported[0].startswith("http://127.0.0.1:")
    assert reported[0].endswith(loomy_flow.QR_PATH)
    page = httpx.get(reported[0], timeout=10.0)
    assert page.status_code == 200
    assert "loomy-uuid" in page.text or "data:image/jpeg" in page.text

    origin = f"http://127.0.0.1:{urlsplit(reported[0]).port}"
    polled = httpx.get(f"{origin}{loomy_flow.POLL_PATH}", timeout=10.0)
    assert polled.json()["status"] == "done"
    finish(result["_thread"])
    assert not result["_error"], result["_error"]

    assert result["access_token"] == "loomy-session"
    assert result["userid"] == "loomy-user"
    assert result["user_id"] == "loomy-user"
    assert result["nickname"] == "WeChatUser"
    assert int(result["expires_at"]) > time.time() * 1000
    assert seen["auth_query"]["appid"] == loomy_flow.WECHAT_APP_ID
    assert seen["poll_query"]["uuid"] == "loomy-uuid"
    assert seen["auth_body"]["param"]["tcode"] == {"code": "wechat-code"}
    assert seen["skip_body"]["param"]["rcode"] == "rc"

    digest = base64.b64encode(hashlib.md5(seen["auth_content"].encode()).digest()).decode()
    signed = "\n".join(["POST", "/login/thirdAccount/bind/auth", "", digest, "application/json", seen["auth_headers"]["date"], seen["auth_headers"]["nonce"], "", ""])
    expected = base64.b64encode(hmac.new(loomy_flow.ACCOUNT_KEY_SECRET.encode(), signed.encode(), hashlib.sha1).digest()).decode()
    assert seen["auth_headers"]["authorization"] == f"account {loomy_flow.ACCOUNT_KEY_ID}:{expected}"
    assert seen["auth_headers"]["content-md5"] == digest


def test_loomy_can_bind_a_phone_after_wechat_confirmation() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/connect/qrconnect":
            return httpx.Response(200, text='/connect/qrcode/bind-uuid')
        if path == "/connect/qrcode/bind-uuid":
            return httpx.Response(200, content=b"\xff\xd8\xff" + b"q" * 240)
        if path == "/connect/l/qrconnect":
            return httpx.Response(200, text="wx_errcode=405; wx_code='bound-code';")
        if path == "/login/thirdAccount/bind/auth":
            return httpx.Response(200, json={"code": "000000", "data": {"rcode": "rc", "bind": 0}})
        if path == "/login/thirdAccount/bind/sendMsg":
            seen["send"] = payload(request)
            return httpx.Response(200, json={"code": "000000", "data": {"msgid": "mid"}})
        if path == "/login/thirdAccount/bind/checkCode":
            seen["verify"] = payload(request)
            return httpx.Response(200, json={"code": "000000", "data": {"session": "bound-session", "userid": "bound-user", "phone": "13800138000"}})
        return httpx.Response(500)

    result, reported = run_flow(loomy_flow.start_loomy_login, mock_client(handler))
    origin = f"http://127.0.0.1:{urlsplit(reported[0]).port}"
    assert httpx.get(f"{origin}{loomy_flow.POLL_PATH}", timeout=10.0).json()["status"] == "need_phone"
    assert httpx.post(f"{origin}{loomy_flow.COMPLETE_PATH}", json={"action": "send_sms", "phone": "13800138000"}, timeout=10.0).json()["ok"] is True
    assert httpx.post(f"{origin}{loomy_flow.COMPLETE_PATH}", json={"action": "verify_sms", "phone": "13800138000", "code": "123456"}, timeout=10.0).json()["done"] is True
    finish(result["_thread"])

    assert result["access_token"] == "bound-session"
    assert result["phone"] == "13800138000"
    assert seen["send"]["param"]["phone"] == "13800138000"
    assert seen["verify"]["param"]["msgid"] == "mid"


def test_raccoon_qr_page_then_poll_login_and_user_info() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/api/web/auth/v1/login_with_qrcode_code":
            seen["poll_body"] = payload(request)
            if "token" not in seen:
                return httpx.Response(200, json={"code": 0, "data": {"status": "pending"}})
            return httpx.Response(
                200,
                json={"code": 0, "data": {"status": "success", "access_token": _jwt({"exp": int(time.time()) + 3600}), "refresh_token": "rr", "office_identity": "org"}},
            )
        if path == "/api/web/auth/v1/user_info":
            seen["userinfo_headers"] = dict(request.headers)
            return httpx.Response(200, json={"code": 0, "data": {"id": "ra-user", "name": "Raccoon", "phone": "13900139000"}})
        return httpx.Response(500)

    result, reported = run_flow(raccoon_flow.start_raccoon_login, mock_client(handler))
    query = parse_qs(urlsplit(reported[0]).query)
    assert reported[0].startswith("https://xiaohuanxiong.com/login/mp?")
    assert query["appname"] == ["商汤小浣熊官网"]
    assert len(query["code"][0]) == 32
    seen["token"] = True
    finish(result["_thread"])
    assert not result["_error"], result["_error"]

    assert result["access_token"]
    assert result["refresh_token"] == "rr"
    assert result["user_id"] == "ra-user"
    assert result["nickname"] == "Raccoon"
    assert result["office_identity"] == "org"
    assert result["phone"] == "13900139000"
    assert len(result["device_id"]) == 32
    assert int(result["expires_at"]) > time.time() * 1000
    assert seen["poll_body"]["qrcode_code"] == query["code"][0]
    assert seen["userinfo_headers"]["authorization"].startswith("Bearer ")




# -- codearts --------------------------------------------------------------- #
def _verify_dpop(dpop: str, htm: str, htu: str) -> None:
    """Check the proof the flow signed: an ES256 JWS over the method and URL."""
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import ec, utils

    header_b64, payload_b64, signature_b64 = dpop.split(".")
    header = json.loads(base64.urlsafe_b64decode(header_b64 + "=" * (-len(header_b64) % 4)))
    payload = json.loads(base64.urlsafe_b64decode(payload_b64 + "=" * (-len(payload_b64) % 4)))
    assert header["alg"] == "ES256"
    assert header["typ"] == "dpop+jwt"
    assert header["jwk"]["crv"] == "P-256"
    assert payload["htm"] == htm
    assert payload["htu"] == htu
    assert isinstance(payload["jti"], str) and payload["jti"]

    x = int.from_bytes(base64.urlsafe_b64decode(header["jwk"]["x"] + "=" * (-len(header["jwk"]["x"]) % 4)), "big")
    y = int.from_bytes(base64.urlsafe_b64decode(header["jwk"]["y"] + "=" * (-len(header["jwk"]["y"]) % 4)), "big")
    public = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), b"\x04" + x.to_bytes(32, "big") + y.to_bytes(32, "big"))
    raw = base64.urlsafe_b64decode(signature_b64 + "=" * (-len(signature_b64) % 4))
    der = utils.encode_dss_signature(int.from_bytes(raw[:32], "big"), int.from_bytes(raw[32:], "big"))
    public.verify(der, f"{header_b64}.{payload_b64}".encode(), ec.ECDSA(hashes.SHA256()))


def test_codearts_dpop_proof_on_the_token_exchange() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "sts.cn-north-4.myhuaweicloud.com"
        seen["dpop"] = request.headers["dpop"]
        seen["body"] = payload(request)
        return httpx.Response(200, json={"credentials": {"access_key_id": "ak", "secret_access_key": "sk", "security_token": "st", "expiration": "2027-01-02T03:04:05Z"}, "refresh_token": "cr"})

    flow = codearts_flow.start_codearts_login
    captured: dict[str, Any] = {}
    error: list[BaseException] = []
    seen_url = threading.Event()

    def report(url: str) -> None:
        captured["url"] = url
        seen_url.set()

    def target() -> None:
        try:
            captured.update(flow(mock_client(handler), report))
        except BaseException as exc:  # noqa: BLE001
            error.append(exc)
            seen_url.set()

    thread = threading.Thread(target=target, daemon=True)
    thread.start()
    assert seen_url.wait(10)

    login_url = captured["url"]
    assert login_url.startswith("https://codearts.huaweicloud.com/portal/authorize?")
    query = parse_qs(urlsplit(login_url).query)
    assert query["client_id"] == [codearts_flow.CLIENT_ID]
    assert query["code_challenge_method"] == ["S256"]
    assert int(query["port"][0]) >= codearts_flow.MIN_CALLBACK_PORT

    redirect = f"http://127.0.0.1:{query['port'][0]}{codearts_flow.CALLBACK_PATH}"
    httpx.get(f"{redirect}?code=the-code", timeout=10.0)
    finish(thread)
    assert not error, error

    assert captured["access_key_id"] == "ak"
    assert captured["security_token"] == "st"
    assert captured["refresh_token"] == "cr"
    assert captured["code_verifier"]
    assert captured["dpop_private_key_jwk"]["crv"] == "P-256"
    assert seen["body"]["code"] == "the-code"
    assert seen["body"]["grant_type"] == "authorization_code"
    _verify_dpop(seen["dpop"], "POST", codearts_flow.STS_TOKEN_ENDPOINT)


# -- the manager ------------------------------------------------------------ #
def _manager(tmp_path: Any, client: httpx.Client) -> LoginManager:
    settings = Settings()
    return LoginManager(settings=settings, client=client)


def _wait_for(manager: LoginManager, session_id: str, timeout: float = 10.0) -> Any:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        session = manager.poll(session_id)
        assert session is not None
        if session.status != "pending":
            return session
        time.sleep(0.02)
    raise AssertionError("the session never finished")


def test_manager_files_codex_and_replaces_the_same_chatgpt_account(tmp_path: Any) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        id_token = _jwt({"https://api.openai.com/auth": {"chatgpt_account_id": "acct-codex"}})
        return httpx.Response(200, json={"access_token": "oa", "refresh_token": "or", "expires_in": 3600, "id_token": id_token})

    manager = _manager(tmp_path, mock_client(handler))
    try:
        saved_accounts = []
        for index in range(2):
            session = manager.start("codex")
            deadline = time.monotonic() + 10.0
            while not session.login_url and time.monotonic() < deadline:
                current = manager.poll(session.id)
                assert current is not None
                session = current
            assert session.login_url

            query = parse_qs(urlsplit(session.login_url).query)
            callback_hit(session.login_url, codex_flow.CALLBACK_PATH, code=f"code-{index}", state=query["state"][0])
            done = _wait_for(manager, session.id)
            assert done.status == "succeeded"
            saved_accounts.append(done.account_id)

        config = json.loads((tmp_path / "config.json").read_text(encoding="utf-8"))
        accounts = config["providers"]["codex"]["accounts"]
        assert len(accounts) == 1
        assert accounts[0]["access_token"] == "oa"
        assert accounts[0]["refresh_token"] == "or"
        assert accounts[0]["account_id"] == "acct-codex"
        assert accounts[0]["id"] == saved_accounts[0]
        assert saved_accounts[0] == saved_accounts[1]
    finally:
        manager.close()


def test_manager_files_the_account_and_replaces_on_relogin(tmp_path: Any) -> None:
    register_count = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/authorize/device"):
            return httpx.Response(200, json={"device_code": "dc", "user_code": "UC", "verification_uri": "https://web.workos.com", "expires_in": 900, "interval": 1})
        if request.url.path.endswith("/authenticate"):
            return httpx.Response(200, json={"access_token": "raw", "refresh_token": "wr"})
        register_count["n"] += 1
        return httpx.Response(200, json={"success": True, "data": {"accessToken": f"workos:jwt{register_count['n']}", "refreshToken": "cr", "userInfo": {"clineUserId": "usr-1", "email": "a@example.com"}}})

    manager = _manager(tmp_path, mock_client(handler))
    first = manager.start("cline")
    done = _wait_for(manager, first.id)
    assert done.status == "succeeded"
    assert done.account_id.startswith("cline-")

    config = json.loads((tmp_path / "config.json").read_text(encoding="utf-8"))
    accounts = config["providers"]["cline"]["accounts"]
    assert len(accounts) == 1
    assert accounts[0]["access_token"] == "workos:jwt1"
    assert accounts[0]["account_id"] == "usr-1"

    # The same station identity must replace the account, not stack a second one.
    second = manager.start("cline")
    done = _wait_for(manager, second.id)
    assert done.status == "succeeded"
    assert done.account_id == accounts[0]["id"]

    config = json.loads((tmp_path / "config.json").read_text(encoding="utf-8"))
    accounts = config["providers"]["cline"]["accounts"]
    assert len(accounts) == 1
    assert accounts[0]["access_token"] == "workos:jwt2"
    assert accounts[0]["id"] == done.account_id
    manager.close()


def test_manager_reports_a_flow_failure(tmp_path: Any) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/authorize/device"):
            return httpx.Response(200, json={"device_code": "dc", "user_code": "UC", "verification_uri": "https://web.workos.com", "expires_in": 900, "interval": 1})
        return httpx.Response(400, json={"error": "access_denied"})

    manager = _manager(tmp_path, mock_client(handler))
    session = manager.start("cline")
    done = _wait_for(manager, session.id)
    assert done.status == "failed"
    assert "access_denied" in done.message
    assert not (tmp_path / "config.json").exists()
    manager.close()


def test_manager_rejects_an_unknown_station(tmp_path: Any) -> None:
    manager = _manager(tmp_path, mock_client(lambda request: httpx.Response(500)))
    with pytest.raises(LoginError) as excinfo:
        manager.start("nope")
    assert "nope" in str(excinfo.value)
    manager.close()


def test_every_registered_flow_imports() -> None:
    assert set(login_providers()) == {"buddy", "cline", "codearts", "codex", "eac", "gemini", "kilo", "lobsterai", "loomy", "minimax", "qoder", "qodercn", "raccoon", "trae", "workbuddy", "zcode",
                                     "anthropic", "github-copilot", "kimi-coding", "meta", "openai", "openrouter", "radius", "xai"}
