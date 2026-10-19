"""The EAC lane: how one request is signed, what the wire sees, and what an unauthorized install gets."""

from __future__ import annotations

import json
import shutil
import tempfile
import uuid
from pathlib import Path
from typing import Any

import httpx
import pytest

from hoshimi.core.catalog import eac_spec
from hoshimi.core.config import ProviderConfig, Settings
from hoshimi.core.eac_user import (
    clear_eac_user,
    read_eac_user,
    write_eac_user,
)
from hoshimi.core.login import eac as eac_flow
from hoshimi.core.login.eac import _gateway_root, _resolve_account, start_eac_login
from hoshimi.core.providers import PROVIDER_CLASSES, build_providers
from hoshimi.core.providers.base import Account, AuthFailed, ProviderError
from hoshimi.core.providers.eac import EAC_MODELS, EacProvider, sign_request

SECRET = "s" * 40
BASE = "https://relay.example/v1"
SSE = (
    'data: {"choices":[{"delta":{"content":"hi"}}]}\n\n'
    'data: {"choices":[{"delta":{"content":"!"}}],"usage":{"prompt_tokens":3,"completion_tokens":2,"total_tokens":5}}\n\n'
    "data: [DONE]\n\n"
)


def _account(**extra: Any) -> Account:
    data = {"id": "eac-1", "base_url": BASE, "access_token": "tok-direct"}
    data.update(extra)
    return Account(data, provider_id="eac")


def _provider(account: Account, handler: Any) -> EacProvider:
    return EacProvider(httpx.Client(transport=httpx.MockTransport(handler)), [account])


def make_tmp_dir() -> Path:
    """A managed test directory rooted under the platform tempdir.

    ``tempfile.TemporaryDirectory()`` calls ``tempfile.mkdtemp`` with mode
    ``0o700``, which on Windows produces an ACL the local sandbox can read
    but not write to. Plain ``os.makedirs`` keeps the parent's ACL — which
    is what makes the directory actually usable. Cleanup uses ignore_errors
    so a sandbox block on rm does not mask a real test failure.
    """
    target = Path(tempfile.gettempdir()) / f"hoshimi_eac_{uuid.uuid4().hex[:12]}"
    target.mkdir(parents=True, exist_ok=False)
    return target


def _settings_with_eac(account: dict[str, Any]) -> Settings:
    if account.get("base_url") and not account.get("access_token") and not account.get("signing_secret"):
        account = {**account, "signing_secret": SECRET}
    settings = Settings(host="127.0.0.1", port=0)
    settings.providers["eac"] = ProviderConfig(id="eac", enabled=True, accounts=[account])
    return settings


# -- the wire ---------------------------------------------------------------- #


def test_signature_layout_is_the_gateway_s_wild() -> None:
    headers = sign_request(SECRET, method="POST", path="/v1/chat/completions", body='{"a": 1}', now=1700000000123)
    assert headers["x-ofm-timestamp"] == "1700000000123"
    assert headers["x-ofm-signature"] == "a73b4b48beb756a3a9c35364b9eb57d117e03fa714fcc4ea8c5895c214bd219f"
    assert sign_request(SECRET, method="post", path="/v1/models", body="", now=1) == sign_request(
        SECRET, method="POST", path="/v1/models", body="", now=1
    )
    assert sign_request(SECRET, method="GET", path="/v1/models", body="", now=1)["x-ofm-signature"] != headers["x-ofm-signature"]


def test_headers_pick_the_mode_and_carry_the_user_token() -> None:
    provider = EacProvider(httpx.Client(), [_account()])
    direct = provider._headers(_account(user_token="ghu_abc"), method="POST", url=f"{BASE}/chat/completions", body="{}")
    assert direct["Authorization"] == "Bearer tok-direct"
    assert "x-ofm-user" not in direct
    assert "x-ofm-signature" not in direct
    assert direct["Accept"] == "text/event-stream"

    worker_account = _account(access_token="", signing_secret=SECRET, user_token="ghu_abc")
    worker = provider._headers(worker_account, method="GET", url=f"{BASE}/models", body="")
    assert "Authorization" not in worker
    assert worker["x-ofm-signature"]
    assert worker["x-ofm-timestamp"]
    assert worker["x-ofm-user"] == "ghu_abc"
    assert worker["Accept"] == "application/json"

    with pytest.raises(ProviderError) as missing:
        provider._headers(_account(access_token=""), method="POST", url=f"{BASE}/chat/completions", body="{}")
    assert "access_token" in missing.value.message


def test_direct_turn_sends_the_bearer_and_parses_the_stream() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["authorization"] = request.headers.get("authorization")
        seen["user"] = request.headers.get("x-ofm-user")
        seen["body"] = request.content.decode("utf-8")
        return httpx.Response(200, text=SSE)

    provider = _provider(_account(user_token="ghu_abc"), handler)
    chunks = list(provider.stream_raw(provider.accounts[0], "z-ai/glm-5.3", {"messages": [{"role": "user", "content": "x"}]}))

    assert seen["path"] == "/v1/chat/completions"
    assert seen["authorization"] == "Bearer tok-direct"
    assert seen["user"] is None
    assert '"model": "z-ai/glm-5.3"' in seen["body"].replace('"model":"', '"model": "')
    assert [chunk["type"] for chunk in chunks] == ["text_delta", "text_delta", "usage", "finish"]
    assert chunks[0]["text"] == "hi"
    assert chunks[3]["reason"] == "stop"


def test_worker_turn_signs_the_bytes_it_sends() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["content"] = request.content
        seen["path"] = request.url.path
        seen["timestamp"] = request.headers.get("x-ofm-timestamp")
        seen["signature"] = request.headers.get("x-ofm-signature")
        seen["authorization"] = request.headers.get("authorization")
        return httpx.Response(200, text=SSE)

    account = _account(access_token="", signing_secret=SECRET)
    provider = _provider(account, handler)
    list(provider.stream_raw(account, "moonshotai/kimi-k3", {"messages": []}))

    assert seen["authorization"] is None
    recomputed = sign_request(
        SECRET,
        method="POST",
        path=seen["path"],
        body=seen["content"].decode("utf-8"),
        now=int(seen["timestamp"]),
    )
    assert seen["signature"] == recomputed["x-ofm-signature"]


def test_a_json_answer_is_not_read_as_an_empty_stream() -> None:
    provider = _provider(_account(), lambda request: httpx.Response(200, json={"choices": [{"delta": {"content": "ok"}}], "usage": {"prompt_tokens": 1, "completion_tokens": 1}}))
    chunks = list(provider.stream_raw(provider.accounts[0], "z-ai/glm-5.3", {"messages": []}))
    assert [chunk["type"] for chunk in chunks] == ["text_delta", "usage", "finish"]


def test_a_turn_without_a_base_url_says_so() -> None:
    provider = _provider(Account({"id": "eac-2", "access_token": "tok"}, provider_id="eac"), lambda request: httpx.Response(200, text=""))
    with pytest.raises(ProviderError) as no_base:
        list(provider.stream_raw(provider.accounts[0], "z-ai/glm-5.3", {"messages": []}))
    assert "base_url" in no_base.value.message


def test_an_unauthorized_install_gets_the_login_hint() -> None:
    provider = _provider(
        _account(signing_secret=SECRET),
        lambda request: httpx.Response(401, json={"error": {"code": "AuthorizationRequired"}}),
    )
    with pytest.raises(AuthFailed) as refused:
        list(provider.stream_raw(provider.accounts[0], "z-ai/glm-5.3", {"messages": []}))
    assert "user_token" in refused.value.message
    assert refused.value.retryable is False


def test_listing_falls_back_to_the_published_roster() -> None:
    provider = _provider(_account(base_url=""), lambda request: httpx.Response(200, json={}))
    assert provider.models() == list(EAC_MODELS)

    def listing(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/models"
        assert request.headers.get("x-ofm-user") == "ghu_abc"
        return httpx.Response(200, json={"data": [{"id": "z-ai/glm-5.3"}, {"id": "new/model-7b"}]})

    served = _provider(_account(signing_secret=SECRET, user_token="ghu_abc"), listing)
    assert served.models() == ["z-ai/glm-5.3", "new/model-7b"]
    spec = served.specs()["new/model-7b"]
    assert spec.name == "EAC Model 7b"
    assert spec.reasoning is True
    assert spec.efforts == ()


def test_the_lane_is_built_only_with_an_account() -> None:
    client = httpx.Client()
    assert "eac" not in build_providers(client, {})
    assert "eac" in build_providers(client, {"eac": [{"id": "eac-1", "base_url": BASE, "access_token": "tok"}]})
    assert "eac" not in build_providers(client, {"eac": [{"id": "eac-1", "base_url": BASE}]}, disabled={"eac"})
    assert PROVIDER_CLASSES["eac"] is EacProvider
    assert EacProvider.requires_authorization is True
    assert EacProvider.keyless is False
    assert EacProvider.probeable is False


def test_capability_table_matches_the_lane() -> None:
    deepseek = eac_spec("deepseek-ai/deepseek-v4.1-flash", "eac")
    assert (deepseek.name, deepseek.vision, deepseek.max_output) == ("EAC DeepSeek V4.1 Flash", True, 384000)
    assert deepseek.efforts == ("disabled", "low", "high", "max")
    assert deepseek.effort_patch == {"reasoning_effort": "$effort"}
    assert deepseek.effort_off_patch == {"reasoning": {"enabled": False}}
    assert deepseek.can_disable_thinking is True

    glm = eac_spec("z-ai/glm-5.3-flash", "eac")
    assert (glm.name, glm.vision) == ("EAC GLM 5.3 Flash", True)
    assert glm.effort_patch == {"thinking": {"type": "enabled"}, "output_config": {"effort": "$effort"}}
    assert glm.can_disable_thinking is False

    assert eac_spec("z-ai/glm-5.3", "eac").vision is False
    assert eac_spec("moonshotai/kimi-k2.6", "eac").context_window == 262144
    assert eac_spec("openai/gpt-oss-20b", "eac").effort_default == "medium"

    unknown = eac_spec("someone/brand-new-9b", "eac")
    assert (unknown.name, unknown.reasoning, unknown.efforts) == ("EAC Brand New 9b", True, ())
    assert (unknown.context_window, unknown.max_output) == (131072, 32768)


# -- the new opencode/* relay ids added in upstream v2.5.0 ------------------- #


def test_opencode_free_ids_are_in_the_published_roster_and_have_their_own_caps() -> None:
    assert "opencode/deepseek-v4.1-flash-free" in EAC_MODELS
    assert "opencode/glm-5.3-flash-free" in EAC_MODELS

    deepseek = eac_spec("opencode/deepseek-v4.1-flash-free", "eac")
    assert deepseek.name == "EAC DeepSeek V4.1 Flash Free"
    assert deepseek.vision is True
    assert deepseek.context_window == 1048576
    assert deepseek.max_output == 384000
    assert deepseek.efforts == ("disabled", "low", "high", "max")
    assert deepseek.effort_patch == {"reasoning_effort": "$effort"}
    assert deepseek.effort_off_patch == {"reasoning_effort": "none"}

    glm = eac_spec("opencode/glm-5.3-flash-free", "eac")
    assert glm.name == "EAC GLM 5.3 Flash Free"
    assert glm.vision is True
    assert glm.max_output == 131072
    assert glm.efforts == ("low", "high", "max")
    assert glm.can_disable_thinking is False


def test_listing_the_relayed_free_ids_clears_through_to_the_catalog() -> None:
    def listing(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "data": [
                    {"id": "opencode/deepseek-v4.1-flash-free"},
                    {"id": "opencode/glm-5.3-flash-free"},
                ]
            },
        )

    served = _provider(_account(signing_secret=SECRET, user_token="ghu_abc"), listing)
    assert served.models() == [
        "opencode/deepseek-v4.1-flash-free",
        "opencode/glm-5.3-flash-free",
    ]
    assert served.specs()["opencode/deepseek-v4.1-flash-free"].vision is True


# -- the eac-user.json file and the per-install token ------------------------ #


def test_write_then_read_eac_user_round_trip() -> None:
    """Tests a write/read round-trip with a fresh temp dir per call."""
    parent = make_tmp_dir()
    try:
        file = parent / "eac-user.json"
        record = {"token": "ghu_test", "login": "octocat", "avatar": "https://example/avatar.png"}
        assert write_eac_user(record, path=file) is True
        reread = read_eac_user(file)
        assert reread is not None
        assert reread["token"] == "ghu_test"
        assert reread["login"] == "octocat"
        assert reread["avatar"] == "https://example/avatar.png"
        assert reread["savedAt"] > 0
    finally:
        shutil.rmtree(parent, ignore_errors=True)


def test_read_eac_user_ignores_blank_token() -> None:
    parent = make_tmp_dir()
    try:
        file = parent / "eac-user.json"
        file.write_text(json.dumps({"token": "", "login": "x"}), encoding="utf-8")
        assert read_eac_user(file) is None
    finally:
        shutil.rmtree(parent, ignore_errors=True)


def test_read_eac_user_handles_a_missing_or_corrupt_file() -> None:
    parent = make_tmp_dir()
    try:
        missing = parent / "missing.json"
        assert read_eac_user(missing) is None
        corrupt = parent / "corrupt.json"
        corrupt.write_text("not-json{", encoding="utf-8")
        assert read_eac_user(corrupt) is None
    finally:
        shutil.rmtree(parent, ignore_errors=True)


def test_clear_eac_user_is_idempotent() -> None:
    parent = make_tmp_dir()
    try:
        file = parent / "eac-user.json"
        assert clear_eac_user(file) is True
        assert clear_eac_user(file) is True
        file.write_text("{}", encoding="utf-8")
        assert clear_eac_user(file) is True
    finally:
        shutil.rmtree(parent, ignore_errors=True)


def test_write_eac_user_rejects_an_empty_token() -> None:
    parent = make_tmp_dir()
    try:
        file = parent / "eac-user.json"
        assert write_eac_user({}, path=file) is False
        assert write_eac_user({"token": ""}, path=file) is False
        assert not file.exists()
    finally:
        shutil.rmtree(parent, ignore_errors=True)


def test_provider_falls_back_to_the_install_token(monkeypatch: Any) -> None:
    parent = make_tmp_dir()
    try:
        file = parent / "eac-user.json"
        write_eac_user({"token": "ghu_install", "login": "octocat"}, path=file)
        monkeypatch.setattr(
            "hoshimi.core.providers.eac.load_eac_user_token",
            lambda: read_eac_user(file)["token"] if read_eac_user(file) else "",
        )
        provider = EacProvider(httpx.Client(), [_account(signing_secret=SECRET)])
        headers = provider._headers(_account(signing_secret=SECRET), method="GET", url=f"{BASE}/models", body="")
        assert headers["x-ofm-user"] == "ghu_install"
    finally:
        shutil.rmtree(parent, ignore_errors=True)


def test_provider_account_token_wins_over_install_token(monkeypatch: Any) -> None:
    parent = make_tmp_dir()
    try:
        file = parent / "eac-user.json"
        write_eac_user({"token": "from_install", "login": "x"}, path=file)
        monkeypatch.setattr(
            "hoshimi.core.providers.eac.load_eac_user_token",
            lambda: read_eac_user(file)["token"],
        )
        provider = EacProvider(httpx.Client(), [_account(signing_secret=SECRET, user_token="from_account")])
        headers = provider._headers(_account(signing_secret=SECRET, user_token="from_account"), method="GET", url=f"{BASE}/models", body="")
        assert headers["x-ofm-user"] == "from_account"
    finally:
        shutil.rmtree(parent, ignore_errors=True)


# -- the EAC login flow ----------------------------------------------------- #


def test_gateway_root_strips_v1() -> None:
    assert _gateway_root({"base_url": "https://relay.example/v1"}) == "https://relay.example"
    assert _gateway_root({"base_url": "https://relay.example/v1/"}) == "https://relay.example"
    assert _gateway_root({"base_url": "https://relay.example"}) == "https://relay.example"
    assert _gateway_root({}) == ""


def test_resolve_account_picks_the_first_one() -> None:
    eac_flow.bind_settings(lambda: None)
    assert _resolve_account() == {"id": "eac-default", "relay": "bundled"}
    settings = _settings_with_eac({"id": "eac-1", "base_url": BASE})
    eac_flow.bind_settings(lambda: settings)
    account = _resolve_account()
    assert account is not None
    assert account["id"] == "eac-1"
    eac_flow.bind_settings(None)


def test_fresh_login_uses_the_bundled_relay_reference() -> None:
    eac_flow.bind_settings(lambda: Settings(host="127.0.0.1", port=0))
    try:
        assert _resolve_account() == {"id": "eac-default", "relay": "bundled"}
    finally:
        eac_flow.bind_settings(None)


def test_login_flow_rejects_an_account_with_no_base_url() -> None:
    eac_flow.bind_settings(lambda: _settings_with_eac({"id": "eac-1", "access_token": "x"}))
    with pytest.raises(Exception) as no_base:
        start_eac_login(httpx.Client(), lambda _url: None)
    assert "no usable relay" in str(no_base.value)
    eac_flow.bind_settings(None)


def test_login_flow_opens_the_browser_and_persists_the_token(monkeypatch: Any) -> None:
    monkeypatch.setattr(eac_flow, "poll_pause", lambda _seconds: None)
    parent = make_tmp_dir()
    try:
        file = parent / "eac-user.json"
        monkeypatch.setattr("hoshimi.core.login.eac.write_eac_user", lambda record, path=None: write_eac_user(record, path=file))
        monkeypatch.setattr("hoshimi.core.login.eac.read_eac_user", lambda path=None: read_eac_user(file))

        eac_flow.bind_settings(lambda: _settings_with_eac({"id": "eac-1", "base_url": BASE}))

        seen_url: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/auth/github/start":
                return httpx.Response(302, headers={"location": "https://github.com/login/oauth/authorize"})
            if request.url.path == "/auth/poll":
                return httpx.Response(200, json={"status": "ok", "token": "ghu_login", "login": "octocat", "avatar": "pic"})
            return httpx.Response(404, json={"status": "unknown"})

        client = httpx.Client(transport=httpx.MockTransport(handler))
        try:
            credential = start_eac_login(client, seen_url.append)
        finally:
            eac_flow.bind_settings(None)

        assert seen_url and seen_url[0].startswith("https://relay.example/auth/github/start?link=")
        assert credential["user_token"] == "ghu_login"
        assert credential["user_login"] == "octocat"
        assert credential["account_id"] == "eac-1"
        persisted = read_eac_user(file)
        assert persisted is not None
        assert persisted["token"] == "ghu_login"
    finally:
        shutil.rmtree(parent, ignore_errors=True)


def test_login_flow_keeps_polling_pending_until_the_gateway_answers(monkeypatch: Any) -> None:
    monkeypatch.setattr(eac_flow, "poll_pause", lambda _seconds: None)
    parent = make_tmp_dir()
    try:
        file = parent / "eac-user.json"
        monkeypatch.setattr("hoshimi.core.login.eac.write_eac_user", lambda record, path=None: write_eac_user(record, path=file))
        monkeypatch.setattr("hoshimi.core.login.eac.read_eac_user", lambda path=None: read_eac_user(file))

        eac_flow.bind_settings(lambda: _settings_with_eac({"id": "eac-1", "base_url": BASE}))

        polls = {"count": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/auth/github/start":
                return httpx.Response(302, headers={"location": "https://github.com/"})
            polls["count"] += 1
            if polls["count"] < 2:
                return httpx.Response(200, json={"status": "pending"})
            return httpx.Response(200, json={"status": "ok", "token": "ghu_late", "login": ""})

        client = httpx.Client(transport=httpx.MockTransport(handler))
        try:
            credential = start_eac_login(client, lambda _url: None)
        finally:
            eac_flow.bind_settings(None)

        assert polls["count"] >= 2
        assert credential["user_token"] == "ghu_late"
    finally:
        shutil.rmtree(parent, ignore_errors=True)


def test_login_flow_reports_unstarred_without_looping(monkeypatch: Any) -> None:
    monkeypatch.setattr(eac_flow, "poll_pause", lambda _seconds: None)
    parent = make_tmp_dir()
    try:
        monkeypatch.setattr("hoshimi.core.login.eac.write_eac_user", lambda record, path=None: False)
        eac_flow.bind_settings(lambda: _settings_with_eac({"id": "eac-1", "base_url": BASE}))

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/auth/github/start":
                return httpx.Response(302, headers={"location": "https://github.com/"})
            return httpx.Response(200, json={"status": "unstarred"})

        client = httpx.Client(transport=httpx.MockTransport(handler))
        try:
            with pytest.raises(Exception) as refused:
                start_eac_login(client, lambda _url: None)
        finally:
            eac_flow.bind_settings(None)
        assert "starred" in str(refused.value)
    finally:
        shutil.rmtree(parent, ignore_errors=True)


def test_login_flow_reports_a_non_302_start_as_a_failure(monkeypatch: Any) -> None:
    monkeypatch.setattr(eac_flow, "poll_pause", lambda _seconds: None)
    parent = make_tmp_dir()
    try:
        monkeypatch.setattr("hoshimi.core.login.eac.write_eac_user", lambda record, path=None: True)
        eac_flow.bind_settings(lambda: _settings_with_eac({"id": "eac-1", "base_url": BASE}))

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(500, json={"status": "down"})

        client = httpx.Client(transport=httpx.MockTransport(handler))
        try:
            with pytest.raises(Exception) as refused:
                start_eac_login(client, lambda _url: None)
        finally:
            eac_flow.bind_settings(None)
        assert "HTTP 500" in str(refused.value)
    finally:
        shutil.rmtree(parent, ignore_errors=True)
