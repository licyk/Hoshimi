"""ChatGPT's two OAuth grants, including renewal after a signing-key outage."""

import json
import time
from urllib.parse import parse_qs, urlencode

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from hoshimi.core.config import Settings, load_settings
from hoshimi.core.context import build_services
from hoshimi.core.login.common import LoginError, sha256_b64url
from hoshimi.core.login.openai import DIRECT_SCOPE, DYNAMIC_CLIENT, ISSUER, RESOURCE, SCOPES
from hoshimi.core.providers.base import ProviderError


class Issuer:
    def __init__(self):
        self.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.registration = "oaiapp_first"
        self.subject = "selected-user"
        self.scope = SCOPES
        self.bad_claims = {}
        self.unavailable = False
        self.reject_once = False
        self.token_error = None
        self.grants = []
        self.authorize = {}
        self.inference = []

    def handle(self, request):
        if request.url.host == "api.openai.com":
            self.inference.append(request.url.path)
            assert request.headers["Authorization"] == "Bearer access"
            if request.method == "GET":
                return httpx.Response(200, json={"models": [{"slug": "model", "display_name": "Model", "visibility": "list"},
                                                            {"slug": "hidden", "display_name": "Hidden", "visibility": "hide"}]})
            assert request.url.path == "/v1/responses"
            assert json.loads(request.content)["store"] is False
            return httpx.Response(200, text='data: {"type":"response.output_text.delta","delta":"hello"}\n\ndata: {"type":"response.completed","response":{"status":"completed","usage":{}}}\n\n')
        assert request.url.host == "auth.openai.com"
        if request.url.path.endswith("openid-configuration"):
            return httpx.Response(200, json={"issuer": ISSUER, "authorization_endpoint": ISSUER + "/authorize",
                                  "token_endpoint": ISSUER + "/token", "jwks_uri": ISSUER + "/keys"})
        if request.url.path == "/keys":
            if self.unavailable:
                return httpx.Response(503)
            key = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(self.key.public_key()))
            return httpx.Response(200, json={"keys": [{**key, "kid": "signing-key"}]})
        assert request.url.path == "/token"
        fields = {key: values[0] for key, values in parse_qs(request.content.decode()).items()}
        self.grants.append(fields)
        if self.token_error:
            status, code = self.token_error
            return httpx.Response(status, json={"error": code, "error_description": "private upstream text"}, headers={"x-request-id": "req-test"})
        if self.reject_once:
            self.reject_once = False
            return httpx.Response(400, json={"error": "invalid_grant"})
        assert fields["resource"] == RESOURCE and fields["client_id"] == self.registration
        now = int(time.time())
        claims = {"iss": ISSUER, "aud": self.registration, "sub": self.subject, "iat": now, "exp": now + 300}
        if fields["grant_type"] == "authorization_code":
            assert sha256_b64url(fields["code_verifier"]) == self.authorize["code_challenge"]
            assert self.authorize["redirect_uri"] == fields["redirect_uri"]
            claims["nonce"] = self.authorize["nonce"]
        claims.update(self.bad_claims)
        payload = {"scope": self.scope, "id_token": jwt.encode(claims, self.key, algorithm="RS256", headers={"kid": "signing-key"})}
        if DIRECT_SCOPE in self.scope.split():
            payload.update(access_token="access", token_type="Bearer", refresh_token=f"refresh-{len(self.grants)}", expires_in=3600)
        return httpx.Response(200, json=payload)


@pytest.fixture
def gateway():
    issuer = Issuer()
    with httpx.Client(transport=httpx.MockTransport(issuer.handle)) as client:
        services = build_services(Settings())
        services.login._client = client
        try:
            yield services, issuer, client
        finally:
            services.close()


def wait_for(predicate):
    end = time.monotonic() + 5
    while time.monotonic() < end:
        if predicate():
            return
        time.sleep(0.01)
    pytest.fail("login session did not reach the expected state")


def login_url(session, issuer, previous_url=""):
    wait_for(lambda: session.status != "pending" or session.waiting_for == "callback_url" and session.login_url != previous_url)
    assert session.status == "pending", session.message
    issuer.authorize = dict(httpx.URL(session.login_url).params)
    return issuer.authorize


def callback(params, **fields):
    return params["redirect_uri"] + "?" + urlencode({"code": "one-use", "state": params["state"], **fields})


def test_browser_registration_retry_and_account_relogin(gateway):
    services, issuer, client = gateway
    manager = services.login
    session = manager.start("openai")
    params = login_url(session, issuer)
    assert params["client_id"] == DYNAMIC_CLIENT and params["agent_name_hint"] == "Hoshimi"
    assert params["ext_agent_host_id"].startswith("urn:uuid:")
    with httpx.Client(trust_env=False) as browser:
        assert browser.get(callback(params, state="old-attempt")).status_code == 400
        assert browser.get(callback(params) + "&state=duplicate").status_code == 400
        assert browser.get(callback(params), headers={"Host": "wrong-host"}).status_code == 404
        issuer.reject_once = True
        first_url = session.login_url
        page = browser.get(callback(params, client_id=issuer.registration))
        assert page.status_code == 200 and "Authorization received" in page.text
    retry = login_url(session, issuer, first_url)
    assert retry["client_id"] == issuer.registration and "agent_name_hint" not in retry
    assert retry["state"] != params["state"] and retry["nonce"] != params["nonce"]
    manager.submit(session.id, callback(retry))
    wait_for(lambda: session.status != "pending")
    assert session.status == "succeeded", session.message
    lane = services.registry.providers["openai"]
    lane.client = client
    assert lane.fetch_models() == ["model"]
    assert any(chunk.get("text") == "hello" for chunk in lane.stream_raw(lane.accounts[0], "model", {"messages": [{"role": "user", "content": "Hello"}]}))
    assert issuer.inference == ["/v1/models", "/v1/responses"]

    relogin = manager.start("openai", account_id=session.account_id)
    params = login_url(relogin, issuer)
    assert params["client_id"] == issuer.registration
    manager.submit(relogin.id, callback(params))
    wait_for(lambda: relogin.status != "pending")
    assert relogin.status == "succeeded" and relogin.account_id == session.account_id
    accounts = load_settings().providers["openai"].accounts
    assert len(accounts) == 1 and accounts[0]["refresh_token"] == "refresh-3"


def test_identity_only_and_explicit_consent(gateway):
    services, issuer, _ = gateway
    manager = services.login
    session = manager.start("openai")
    params = login_url(session, issuer)
    with pytest.raises(LoginError, match="complete callback"):
        manager.submit(session.id, "just-the-code")
    issuer.scope = "openid profile"
    manager.submit(session.id, callback(params, client_id=issuer.registration))
    wait_for(lambda: session.status != "pending")
    assert session.status == "succeeded" and session.plan_authorized is False
    lane = services.registry.providers["openai"]
    assert lane.fetch_models() == [] and lane._stream_candidates("model") == []
    assert "access_token" not in lane.accounts[0].data

    before = load_settings().providers["openai"].accounts
    for changes in ({"client_id": "different"}, {"error": "access_denied"}, {}):
        relogin = manager.start("openai", account_id=session.account_id)
        params = login_url(relogin, issuer)
        assert params["prompt"] == "consent"
        issuer.subject = "different"
        manager.submit(relogin.id, callback(params, **changes))
        wait_for(lambda current=relogin: current.status != "pending")
        assert relogin.status == "failed" and load_settings().providers["openai"].accounts == before
    issuer.scope, issuer.subject = SCOPES, "selected-user"
    relogin = manager.start("openai", account_id=session.account_id)
    manager.submit(relogin.id, callback(login_url(relogin, issuer)))
    wait_for(lambda: relogin.status != "pending")
    assert relogin.plan_authorized is True


@pytest.mark.parametrize("claims", [{"aud": "other"}, {"iss": "other"}, {"nonce": "old"}, {"exp": 1}, {"azp": "other"}])
def test_invalid_identity_is_not_saved(gateway, claims):
    services, issuer, _ = gateway
    session = services.login.start("openai")
    issuer.bad_claims = claims
    services.login.submit(session.id, callback(login_url(session, issuer), client_id=issuer.registration))
    wait_for(lambda: session.status != "pending")
    assert "identity verification failed" in session.message
    assert "openai" not in load_settings().providers


def test_refresh_resumes_after_restart_without_reusing_grant(gateway):
    services, issuer, client = gateway
    session = services.login.start("openai")
    services.login.submit(session.id, callback(login_url(session, issuer), client_id=issuer.registration))
    wait_for(lambda: session.status != "pending")
    lane = services.registry.providers["openai"]
    lane.client = client
    issuer.unavailable = True
    with pytest.raises(ProviderError, match="signing-key service"):
        lane.renew_account(lane.accounts[0], expected_revision=0)
    saved = load_settings().providers["openai"].accounts[0]
    assert saved["pending_refresh"] and "refresh-2" not in json.dumps(saved)
    assert lane.inference_accounts() == []
    issuer.unavailable = False
    restored = build_services(load_settings())
    try:
        lane = restored.registry.providers["openai"]
        lane.client = client
        renewal = restored.refresher.run_once("openai")
        assert renewal[0].persisted and renewal[0].ok
        assert len(issuer.grants) == 2 and issuer.grants[-1]["grant_type"] == "refresh_token"
        saved = load_settings().providers["openai"].accounts[0]
        assert "pending_refresh" not in saved and saved["refresh_token"] == "refresh-2"
        assert len(lane.inference_accounts()) == 1
    finally:
        restored.close()


@pytest.mark.parametrize(("failure", "terminal"), [((400, "invalid_grant"), True), ((401, "invalid_client"), False), ((503, "unavailable"), False)])
def test_refresh_verdict_preserves_registration(gateway, failure, terminal):
    services, issuer, client = gateway
    session = services.login.start("openai")
    services.login.submit(session.id, callback(login_url(session, issuer), client_id=issuer.registration))
    wait_for(lambda: session.status != "pending")
    lane = services.registry.providers["openai"]
    lane.client = client
    issuer.token_error = failure
    result, = services.refresher.run_once("openai", force=True)
    assert result.terminal is terminal and not result.ok
    assert failure[1] in result.error and "req-test" in result.error and "private upstream text" not in result.error
    saved = load_settings().providers["openai"].accounts[0]
    assert saved["client_id"] == issuer.registration
    assert ("refresh_token" not in saved) is terminal
    assert bool(saved.get("reauth_required")) is terminal


def test_separate_registrations_for_the_same_subject(gateway):
    services, issuer, _ = gateway
    for client_id in ("first", "second"):
        issuer.registration = client_id
        session = services.login.start("openai")
        services.login.submit(session.id, callback(login_url(session, issuer), client_id=client_id))
        wait_for(lambda active=session: active.status != "pending")
        assert session.status == "succeeded", session.message
    accounts = load_settings().providers["openai"].accounts
    assert [a["client_id"] for a in accounts] == ["first", "second"]
