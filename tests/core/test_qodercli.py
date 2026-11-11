"""The ported Qoder CLI protocol: signing, encoding, catalogue, wire and recovery.

Every assertion here pins a behaviour the plugin settled against the live
service: the COSY signature's byte order, the custom Base64 block rotation, the
PAT exchange shape, the node rotation on code 110, the bounded busy recovery on
10605, and the thinking/DSML cleanup. MockTransport answers the station, so the
tests never touch an account.
"""

from __future__ import annotations

import base64
import hashlib
import json
import queue
import threading
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from hoshimi.core.login.common import LoginError, LoginReporter
from hoshimi.core.login.qodercli import start_qodercli_login
from hoshimi.core.providers.base import Account, RefreshTokenRejected
from hoshimi.core.providers.qodercli import QoderCliProvider
from hoshimi.core.qoder import catalog, cosy, encoding, endpoints, protocol
from hoshimi.core.qoder.auth import credential_from_pat, exchange_job_token, fetch_user_info
from hoshimi.core.qoder.cosy_version import BUNDLED_GATEWAY_COSY_VERSION, CosyVersion, compare_cli_versions
from hoshimi.core.qoder.region import CN, GLOBAL


def mock_client(handler: Any) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


# -- encoding ---------------------------------------------------------------- #
def test_encoding_round_trips_and_rotates_the_three_blocks() -> None:
    raw = json.dumps({"messages": [{"role": "user", "content": "hello"}]}, separators=(",", ":")).encode()
    encoded = encoding.encode_body(raw)
    assert encoding.decode_body(encoded) == raw.decode()
    # The rotation is observable: tail + middle + head, then the alphabet swap.
    std = base64.b64encode(raw).decode()
    n = len(std)
    a = n // 3
    rotated = std[n - a:] + std[a:n - a] + std[:a]
    table = {ord(encoding.STANDARD_ALPHABET[i]): encoding.CUSTOM_ALPHABET[i] for i in range(len(encoding.STANDARD_ALPHABET))}
    table[ord("=")] = "$"
    assert encoded.decode() == rotated.translate(table)


def test_encoding_handles_empty_and_short_bodies() -> None:
    assert encoding.encode_body(b"") == b""
    assert encoding.decode_body("") == ""
    for raw in (b"a", b"ab", b"abc", b"abcd", "中文".encode()):
        assert encoding.decode_body(encoding.encode_body(raw)) == raw.decode()


# -- COSY signing ------------------------------------------------------------ #
def test_cosy_headers_sign_the_exact_body_and_strip_the_algo_prefix() -> None:
    creds = cosy.CosyCredentials(user_id="uid-1", auth_token="jt-token", name="Neo", email="neo@example.com", machine_id="machine-1")
    body = encoding.encode_body(b'{"a":1}')
    url = "https://api3.qoder.sh/algo/api/v2/service/pro/sse/agent_chat_generation?Encode=1"
    headers = cosy.build_auth_headers(body, url, creds, cosy_version="1.1.72")

    assert headers["Cosy-Sigpath"] == "/api/v2/service/pro/sse/agent_chat_generation"
    assert headers["Cosy-Bodyhash"] == hashlib.md5(body).hexdigest()
    assert headers["Cosy-Bodylength"] == str(len(body))
    assert headers["Cosy-User"] == "uid-1"
    assert headers["Cosy-Machineid"] == headers["Cosy-Machinetoken"] == "machine-1"
    assert headers["Cosy-Clienttype"] == "5"
    assert headers["Cosy-Data-Policy"] == "disagree"
    assert headers["Login-Version"] == "v2"

    scheme, token = headers["Authorization"].split(" ")
    assert scheme == "Bearer"
    assert token.startswith("COSY.")
    payload_b64, sig = token[len("COSY."):].split(".")
    expected = hashlib.md5(
        payload_b64.encode() + b"\n" + headers["Cosy-Key"].encode() + b"\n" + headers["Cosy-Date"].encode() + b"\n" + body + b"\n" + headers["Cosy-Sigpath"].encode()
    ).hexdigest()
    assert sig == expected
    payload = json.loads(base64.b64decode(payload_b64))
    assert payload["version"] == "v1" and payload["cosyVersion"] == "1.1.72" and payload["ideVersion"] == ""


def test_cosy_info_is_aes_cbc_under_an_rsa_encrypted_key(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, str] = {}
    original = cosy.rsa_encrypt_base64

    def spy(key: str) -> str:
        captured["key"] = key
        return original(key)

    monkeypatch.setattr(cosy, "rsa_encrypt_base64", spy)
    creds = cosy.CosyCredentials(user_id="uid-1", auth_token="jt-token", name="Neo", email="neo@example.com")
    headers = cosy.build_auth_headers(None, "https://api3.qoder.sh/algo/api/v2/model/list", creds, cosy_version="1.1.72")
    assert len(captured["key"]) == 16
    assert len(base64.b64decode(headers["Cosy-Key"])) == 128  # 1024-bit RSA

    payload = json.loads(base64.b64decode(headers["Authorization"].split("COSY.")[1].split(".")[0]))
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

    raw = captured["key"].encode()
    decryptor = Cipher(algorithms.AES(raw), modes.CBC(raw)).decryptor()
    plain = decryptor.update(base64.b64decode(payload["info"])) + decryptor.finalize()
    plain = plain[: -plain[-1]]
    info = json.loads(plain)
    assert info == {"uid": "uid-1", "security_oauth_token": "jt-token", "name": "Neo", "aid": "", "email": "neo@example.com"}


def test_cosy_refuses_an_empty_identity() -> None:
    with pytest.raises(ValueError):
        cosy.build_auth_headers(None, "https://api3.qoder.sh/algo/x", cosy.CosyCredentials(user_id="", auth_token="t"), cosy_version="1.1.72")
    with pytest.raises(ValueError):
        cosy.build_auth_headers(None, "https://api3.qoder.sh/algo/x", cosy.CosyCredentials(user_id="u", auth_token=""), cosy_version="1.1.72")


def test_cosy_version_never_downgrades_and_throttles(tmp_path: Path) -> None:
    version = CosyVersion(path=tmp_path / "v.json")
    assert version.get() == BUNDLED_GATEWAY_COSY_VERSION
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(200, json=[{"tag_name": "9.9.9", "draft": False, "prerelease": False}])

    with mock_client(handler) as client:
        assert version.refresh(client) == "9.9.9"
        assert version.refresh(client) == "9.9.9"  # throttled: no second request
    assert len(calls) == 1
    assert compare_cli_versions("1.1.86", "1.1.72") > 0

    def older(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[{"tag_name": "0.0.1"}])

    with mock_client(older) as client:
        assert version.refresh(client, force=True) == "9.9.9"


# -- PAT auth ---------------------------------------------------------------- #
def test_pat_exchange_posts_the_personal_token_without_a_signature() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["body"] = json.loads(request.content)
        seen["headers"] = dict(request.headers)
        return httpx.Response(200, json={"token": "jt-1", "refresh_token": "jrt-1", "expires_in": 86400000})

    with mock_client(handler) as client:
        job = exchange_job_token(client, CN, "pt-abc")
    assert seen["path"] == "/api/v1/jobToken/exchange"
    assert seen["body"] == {"personal_token": "pt-abc"}
    assert seen["headers"]["cosy-clienttype"] == "5"
    assert "authorization" not in seen["headers"]
    assert job.token == "jt-1" and job.refresh_token == "jrt-1"
    assert job.expires_at_ms > 0


def test_credential_from_pat_keeps_the_pat_and_resolves_identity() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/jobToken/exchange"):
            return httpx.Response(200, json={"token": "jt-1", "refresh_token": "jrt-1", "expires_at": "2030-01-01T00:00:00Z"})
        return httpx.Response(200, json={"id": "uid-9", "name": "Neo", "email": "neo@example.com"})

    with mock_client(handler) as client:
        credential = credential_from_pat(client, GLOBAL, "pt-abc", machine_id="m-1")
        identity = fetch_user_info(client, GLOBAL, "jt-1")
    assert credential["personal_token"] == "pt-abc"
    assert credential["access_token"] == credential["security_oauth_token"] == "jt-1"
    assert credential["refresh_token"] == "jrt-1"
    assert credential["uid"] == credential["user_id"] == "uid-9"
    assert credential["machine_id"] == "m-1"
    assert identity.user_id == "uid-9"


def test_pat_exchange_refusal_is_reported() -> None:
    with mock_client(lambda request: httpx.Response(400, json={"errorCode": "BadRequest"})) as client:
        from hoshimi.core.qoder.auth import PatExchangeError

        with pytest.raises(PatExchangeError):
            exchange_job_token(client, GLOBAL, "pt-bad")


# -- region endpoints -------------------------------------------------------- #
def test_region_endpoints_decode_and_prefer_the_remote_agent_node() -> None:
    payload = {
        "inferNodes": ["https://infer.example"],
        "dataNodeMap": {"remote_agent": ["https://agent.example"]},
        "security": ["https://security.example"],
        "centerNodes": ["https://center.example"],
    }
    encoded = encoding.encode_body(json.dumps(payload).encode()).decode()

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/algo/api/v3/service/region/endpoints"
        assert request.headers["authorization"].startswith("Bearer COSY.")
        return httpx.Response(200, text=encoded)

    creds = cosy.CosyCredentials(user_id="u", auth_token="jt", machine_id="m")
    with mock_client(handler) as client:
        resolver = endpoints.EndpointResolver(CN, client, path=Path("/nonexistent-qoder-cache.json"))
        resolved = resolver.refresh(creds)
    assert resolved == "https://agent.example/"
    assert resolver.chat_base_url() == "https://agent.example/"
    candidates = endpoints.chat_endpoint_candidates(resolver.endpoints(creds), CN.base_url)
    assert candidates[0] == CN.base_url
    assert candidates[1] == "https://agent.example/"
    assert "https://infer.example/" in candidates and "https://security.example/" in candidates


def test_rotation_walks_the_candidates_once(tmp_path: Path) -> None:
    payload = {"inferNodes": ["https://infer.example"], "dataNodeMap": {"remote_agent": ["https://agent.example"]}, "security": ["https://security.example"]}

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=encoding.encode_body(json.dumps(payload).encode()).decode())

    creds = cosy.CosyCredentials(user_id="u", auth_token="jt", machine_id="m")
    with mock_client(handler) as client:
        resolver = endpoints.EndpointResolver(GLOBAL, client, path=tmp_path / "nodes.json")
        resolver.refresh(creds)
        attempted: set[str] = set()
        first = resolver.rotate(attempted, creds)
        second = resolver.rotate(attempted, creds)
        third = resolver.rotate(attempted, creds)
        fourth = resolver.rotate(attempted, creds)
    # The configured base stays the first candidate, then infer, then security.
    assert first == GLOBAL.base_url
    assert second == "https://infer.example/"
    assert third == "https://security.example/"
    assert fourth is None


def test_the_served_node_is_persisted_and_reapplied(tmp_path: Path) -> None:
    path = tmp_path / "nodes.json"
    resolver = endpoints.EndpointResolver(CN, httpx.Client(), path=path)
    resolver.remember("https://agent.example")
    fresh = endpoints.EndpointResolver(CN, httpx.Client(), path=path)
    assert fresh.cached_chat_base_url() == "https://agent.example/"
    assert fresh.apply_cached() == "https://agent.example/"
    assert fresh.chat_base_url() == "https://agent.example/"


# -- catalogue --------------------------------------------------------------- #
def test_catalogue_ids_are_display_names_and_context_is_the_largest_option() -> None:
    row = {
        "key": "qmodel_38max",
        "display_name": "Qwen3.8 Max",
        "enable": True,
        "is_vl": True,
        "is_reasoning": True,
        "max_input_tokens": 180000,
        "context_config": {"200K": {"token_count": 200000}, "1M": {"token_count": 1000000, "is_default": False}},
        "thinking_config": {"disabled": {}, "enabled": {"efforts": {"low": {}, "medium": {"is_default": True}, "xhigh": {}}}},
        "source": "system",
    }
    entry = catalog.parse_entry(row)
    assert entry is not None
    assert entry.id == "Qwen3.8Max"
    assert entry.context_window == 1000000
    assert entry.efforts == ("low", "medium", "xhigh", "none")
    assert entry.effort_default == "medium"
    assert entry.can_disable is True
    spec = catalog.spec_for(entry, "qodercli")
    assert spec.vision and spec.reasoning and spec.max_output == 131072
    assert spec.effort_off_id == "none" and spec.effort_patch == {"reasoning_effort": "$effort"}
    assert entry.raw["context_config"]["1M"]["is_default"] is True


def test_toggle_only_models_get_a_single_on_rung() -> None:
    row = {"key": "auto", "display_name": "Auto", "enable": True, "thinking_config": {"enabled": {}}}
    entry = catalog.parse_entry(row)
    assert entry is not None and entry.toggle_only
    assert entry.efforts == ("on",) and entry.can_disable is False
    disabled = catalog.parse_entry({"key": "auto", "display_name": "Auto", "enable": True, "thinking_config": {"enabled": {}, "disabled": {}}})
    assert disabled is not None and disabled.efforts == ("on", "none")


def test_disabled_and_unnamed_rows_are_dropped() -> None:
    assert catalog.parse_entry({"key": "x", "display_name": "X", "enable": False}) is None
    assert catalog.parse_entry({"key": "x", "enable": True}) is None


def test_the_two_static_catalogues_are_not_copies() -> None:
    global_ids = {entry.id for entry in catalog.STATIC_GLOBAL}
    cn_ids = {entry.id for entry in catalog.STATIC_CN}
    assert "Ultimate" in global_ids and "Ultimate" not in cn_ids
    assert "Qwen3.6-Flash" in cn_ids and "Qwen3.6-Flash" not in global_ids
    assert catalog.static_entries(CN) == catalog.STATIC_CN


def test_catalogue_refresh_signs_and_caches(tmp_path: Path) -> None:
    rows = {"chat": [{"key": "lite", "display_name": "Lite", "enable": True, "is_vl": False, "source": "system"}]}

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/algo/api/v2/model/list"
        assert request.url.params["Encode"] == "1"
        assert request.headers["authorization"].startswith("Bearer COSY.")
        return httpx.Response(200, json=rows)

    book = catalog.ModelCatalog(GLOBAL, path=tmp_path / "models.json")
    creds = cosy.CosyCredentials(user_id="u", auth_token="jt", machine_id="m")
    with mock_client(handler) as client:
        assert book.refresh(client, creds, cosy_version="1.1.72") is True
    assert book.ids() == ["Lite"]
    assert book.stale() is False
    reloaded = catalog.ModelCatalog(GLOBAL, path=tmp_path / "models.json")
    assert reloaded.ids() == ["Lite"]
    assert reloaded.config_for("Lite")["key"] == "lite"
    assert reloaded.entry_for("lite") is not None  # a raw key still resolves


# -- request envelope -------------------------------------------------------- #
def test_system_prompt_rides_as_a_leading_message_and_tools_are_mapped() -> None:
    body = {
        "messages": [
            {"role": "system", "content": "rules"},
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "", "tool_calls": [{"id": "call_a|item_b", "function": {"name": "read", "arguments": "{\"path\": \"x\"}"}}]},
            {"role": "tool", "tool_call_id": "call_a|item_b", "content": "file body"},
        ],
        "tools": [{"type": "function", "function": {"name": "read", "description": "Read", "parameters": {"type": "object"}}}],
        "max_tokens": 4096,
    }
    entry = catalog.parse_entry({"key": "lite", "display_name": "Lite", "enable": True})
    envelope = protocol.build_chat_body(body, model_key="lite", model_config={"key": "lite", "source": "system"}, entry=entry, user_id="uid-1")
    assert envelope["system"] == ""
    assert envelope["messages"][0] == {"role": "system", "content": "rules"}
    assert envelope["session_type"] == "qodercli" and envelope["agent_id"] == "agent_common"
    assert envelope["chat_task"] == "FREE_INPUT" and envelope["version"] == "3"
    assert envelope["parameters"]["max_tokens"] == 4096
    assistant = envelope["messages"][2]
    assert assistant["content"] == " "  # placeholder keeps the turn alive
    call_id = assistant["tool_calls"][0]["id"]
    assert protocol.VALID_WIRE_ID.match(call_id) and call_id.startswith("qtc_")
    assert envelope["messages"][3]["tool_call_id"] == call_id
    assert envelope["tools"][0]["function"]["name"] == "read"
    assert envelope["chat_context"]["text"] == "hi"
    assert envelope["business"]["product"] == "cli"


def test_tool_call_ids_are_stable_and_collision_free() -> None:
    mapper = protocol.ToolCallIdMapper()
    assert mapper("call_ok-1") == "call_ok-1"
    first = mapper("call_a|item_b")
    assert mapper("call_a|item_b") == first
    assert mapper("call_a|item_c") != first
    with pytest.raises(ValueError):
        mapper("")


def test_tool_result_images_ride_on_one_user_message_after_the_run() -> None:
    messages = [
        {"role": "assistant", "content": "", "tool_calls": [{"id": "c1", "function": {"name": "read", "arguments": "{}"}}, {"id": "c2", "function": {"name": "read", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "c1", "content": [{"type": "text", "text": "one"}, {"type": "image_url", "image_url": {"url": "data:image/png;base64,AA"}}]},
        {"role": "tool", "tool_call_id": "c2", "content": "two"},
    ]
    out, system = protocol.transform_messages(messages)
    assert system == ""
    assert [row["role"] for row in out] == ["assistant", "tool", "tool", "user"]
    assert out[-1]["content"][0]["text"] == "[1 image returned by the previous tool call]"
    assert out[-1]["content"][1]["image_url"]["url"] == "data:image/png;base64,AA"


def test_thinking_parameters_follow_the_menu_and_the_off_rung() -> None:
    effort_model = catalog.parse_entry({
        "key": "m", "display_name": "M", "enable": True,
        "thinking_config": {"disabled": {}, "enabled": {"efforts": {"low": {}, "high": {}}}},
    })
    parameters, on = protocol.thinking_parameters("high", effort_model, max_tokens=100)
    assert on and parameters == {"max_tokens": 100, "enable_thinking": True, "reasoning_effort": "high"}
    parameters, on = protocol.thinking_parameters("none", effort_model, max_tokens=100)
    assert not on and parameters["enable_thinking"] is False and parameters["reasoning_effort"] == "none"
    parameters, on = protocol.thinking_parameters(None, effort_model, max_tokens=100)
    assert not on and parameters["enable_thinking"] is False

    # No disabled branch: "none" is rejected upstream, so the switch alone is sent.
    forced = catalog.parse_entry({"key": "m", "display_name": "M", "enable": True, "thinking_config": {"enabled": {"efforts": {"low": {}}}}})
    parameters, on = protocol.thinking_parameters("none", forced, max_tokens=100)
    assert not on and "reasoning_effort" not in parameters

    toggle = catalog.parse_entry({"key": "auto", "display_name": "Auto", "enable": True, "thinking_config": {"enabled": {}}})
    parameters, on = protocol.thinking_parameters("on", toggle, max_tokens=100)
    assert on and parameters == {"max_tokens": 100, "enable_thinking": True}


def test_output_ceiling_is_capped_at_the_upstream_maximum() -> None:
    assert protocol.output_ceiling({}) == catalog.MAX_OUTPUT_TOKENS
    assert protocol.output_ceiling({"max_tokens": 1024}) == 1024
    assert protocol.output_ceiling({"max_completion_tokens": 999999}) == catalog.MAX_OUTPUT_TOKENS


def test_record_id_is_stable_for_the_same_request() -> None:
    messages = [{"role": "user", "content": "hi"}]
    first = protocol.chat_record_id("lite", messages, None, 100)
    assert first == protocol.chat_record_id("lite", messages, None, 100)
    assert first != protocol.chat_record_id("lite", messages, None, 200)

# -- the PAT login flow ------------------------------------------------------ #
def pat_handler(request: httpx.Request) -> httpx.Response:
    if request.url.path.endswith("/jobToken/exchange"):
        assert json.loads(request.content) == {"personal_token": "pt-pasted"}
        return httpx.Response(200, json={"token": "jt-1", "refresh_token": "jrt-1", "expires_at": "2030-01-01T00:00:00Z"})
    if request.url.path.endswith("/userinfo"):
        return httpx.Response(200, json={"id": "uid-1", "name": "Neo", "email": "neo@example.com"})
    return httpx.Response(404, json={"code": "404"})


def run_pat_flow(variant: str, token: str | None, handler: Any = pat_handler) -> dict[str, Any]:
    """Drive the flow with a real LoginReporter, as the manager does."""
    inputs: queue.Queue[str] = queue.Queue()
    cancelled = threading.Event()
    updates: list[dict[str, Any]] = []
    reporter = LoginReporter(lambda values: updates.append(dict(values)), inputs=inputs, cancelled=cancelled)
    if token is not None:
        inputs.put(token)
    result: dict[str, Any] = {}
    error: list[BaseException] = []

    def target() -> None:
        try:
            result.update(start_qodercli_login(httpx.Client(transport=httpx.MockTransport(handler)), reporter, variant))
        except BaseException as exc:  # noqa: BLE001 - the test asserts on it
            error.append(exc)

    thread = threading.Thread(target=target, daemon=True)
    thread.start()
    thread.join(10)
    assert not thread.is_alive(), "the login flow never finished"
    result["_updates"] = updates
    result["_error"] = error
    return result


def test_pat_login_reports_the_integrations_page_and_files_the_credential() -> None:
    result = run_pat_flow("qodercli-cn", "pt-pasted")
    assert not result["_error"], result["_error"]
    assert result["personal_token"] == "pt-pasted"
    assert result["access_token"] == "jt-1"
    assert result["uid"] == "uid-1"
    assert result["nickname"] == "Neo"
    assert result["machine_id"]
    urls = [u["login_url"] for u in result["_updates"] if "login_url" in u]
    assert urls == ["https://qoder.com.cn/account/integrations"]
    waiting = [u for u in result["_updates"] if u.get("waiting_for") == "personal_token"]
    assert waiting and "pt-" in waiting[0]["instructions"]
    assert result["_updates"][-1]["waiting_for"] == ""


def test_pat_login_uses_the_cn_hosts() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.host)
        return pat_handler(request)

    result = run_pat_flow("qodercli-cn", "pt-pasted", handler)
    assert not result["_error"], result["_error"]
    assert seen == ["openapi.qoder.com.cn", "openapi.qoder.com.cn"]
    urls = [u["login_url"] for u in result["_updates"] if "login_url" in u]
    assert urls == ["https://qoder.com.cn/account/integrations"]


def test_an_environment_pat_logs_in_without_asking(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("QODER_PERSONAL_ACCESS_TOKEN", "pt-pasted")
    result = run_pat_flow("qodercli", None)
    assert not result["_error"], result["_error"]
    assert result["access_token"] == "jt-1"
    assert not any(u.get("waiting_for") == "personal_token" for u in result["_updates"])


def test_a_token_that_is_not_a_pat_is_refused() -> None:
    result = run_pat_flow("qodercli-cn", "sk-not-a-pat")
    assert result["_error"] and isinstance(result["_error"][0], LoginError)
    assert "pt-" in str(result["_error"][0])


def test_a_refused_exchange_fails_the_login() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"errorCode": "BadRequest", "errorMessage": "Bad request"})

    result = run_pat_flow("qodercli-cn", "pt-pasted", handler)
    assert result["_error"] and isinstance(result["_error"][0], LoginError)
    assert "PAT exchange failed" in str(result["_error"][0])


def test_the_manager_persists_the_pat_account(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from hoshimi.core.config import Settings
    from hoshimi.core.login import LoginManager

    monkeypatch.setenv("HOSHIMI_DATA_DIR", str(tmp_path))
    manager = LoginManager(Settings(), client=httpx.Client(transport=httpx.MockTransport(pat_handler)))
    session = manager.start("qodercli-cn")
    deadline = threading.Event()
    for _ in range(100):
        current = manager.poll(session.id)
        if current is not None and current.waiting_for == "personal_token":
            break
        deadline.wait(0.05)
    assert manager.poll(session.id).waiting_for == "personal_token"
    manager.submit(session.id, "pt-pasted")
    for _ in range(200):
        current = manager.poll(session.id)
        if current is not None and current.status != "pending":
            break
        deadline.wait(0.05)
    current = manager.poll(session.id)
    assert current is not None and current.status == "succeeded", current.message if current else "no session"
    account = manager.settings.providers["qodercli-cn"].accounts[0]
    assert account["personal_token"] == "pt-pasted"
    assert account["access_token"] == "jt-1"
    assert account["uid"] == "uid-1"
    manager.close()

# -- the browser device flow (international lane) ---------------------------- #
def test_the_global_lane_reports_the_device_page_and_polls_until_approved(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("hoshimi.core.login.qodercli.poll_pause", lambda _seconds: None)
    seen: dict[str, Any] = {"polls": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/deviceToken/poll"):
            seen["polls"] += 1
            seen["poll_query"] = dict(request.url.params)
            if seen["polls"] == 1:
                return httpx.Response(404, json={"errorCode": "NotFound"})
            return httpx.Response(200, json={"token": "qt-device", "user_id": "uid-device", "refresh_token": "qr-device", "expires_in": 3600})
        if request.url.path.endswith("/userinfo"):
            return httpx.Response(200, json={"id": "uid-device", "name": "Trinity", "email": "t@example.com"})
        return httpx.Response(404, json={"code": "404"})

    result = run_pat_flow("qodercli", None, handler)
    assert not result["_error"], result["_error"]
    urls = [u["login_url"] for u in result["_updates"] if "login_url" in u]
    assert urls and urls[0].startswith("https://qoder.com/device/selectAccounts?")
    query = parse_qs(urlsplit(urls[0]).query)
    assert query["challenge_method"] == ["S256"] and query["machine_id"]
    assert seen["poll_query"]["nonce"] == query["nonce"][0]
    assert seen["poll_query"]["verifier"]
    assert result["access_token"] == "qt-device"
    assert result["refresh_token"] == "qr-device"
    assert result["uid"] == "uid-device"
    assert result["nickname"] == "Trinity"
    assert "personal_token" not in result


def test_a_pasted_pat_wins_over_the_browser_flow(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("hoshimi.core.login.qodercli.poll_pause", lambda _seconds: None)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/deviceToken/poll"):
            raise AssertionError("the device poll must not run once a token was pasted")
        return pat_handler(request)

    result = run_pat_flow("qodercli", "pt-pasted", handler)
    assert not result["_error"], result["_error"]
    assert result["personal_token"] == "pt-pasted"
    assert result["access_token"] == "jt-1"


def test_a_device_credential_renews_against_the_center_host() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/user/refresh_token"):
            seen["host"] = request.url.host
            seen["body"] = json.loads(request.content)
            seen["auth"] = request.headers.get("authorization")
            return httpx.Response(200, json={"token": "qt-new", "refresh_token": "qr-new", "expires_in": 7200})
        return httpx.Response(404, json={"code": "404", "message": "unexpected path"})

    account = Account({"id": "a", "access_token": "qt-old", "refresh_token": "qr-old", "uid": "uid-1"}, provider_id="qodercli")
    with mock_client(handler) as client:
        lane = QoderCliProvider(client, [account])
        assert lane.refresh_account(account) is True
    assert seen["host"] == "center.qoder.sh"
    assert seen["body"] == {"refreshToken": "qr-old"}
    assert seen["auth"] == "Bearer qt-old"
    assert account.get("access_token") == "qt-new"
    assert account.get("refresh_token") == "qr-new"


def test_a_refused_device_refresh_is_terminal() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"code": "105", "message": "Login expired"})

    account = Account({"id": "a", "access_token": "qt-old", "refresh_token": "qr-old", "uid": "uid-1"}, provider_id="qodercli")
    with mock_client(handler) as client:
        lane = QoderCliProvider(client, [account])
        with pytest.raises(RefreshTokenRejected):
            lane.refresh_account(account)
