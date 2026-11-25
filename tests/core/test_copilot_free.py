"""Copilot's plan decides what an account may run, and the Auto route saves the free one.

The fixture below is the ``/models`` listing a real Copilot Free account
answered with: 48 models, every one of them ``model_picker_enabled: false``,
including 35 the account cannot run. It is trimmed to the fields the lane
reads, and it keeps the rows that disagree with each other, because that
disagreement is the bug: a free account is offered Claude Opus and GPT-6
alongside the models it can actually use.
"""

from __future__ import annotations

import httpx
import pytest

from hoshimi.core.providers.base import Account
from hoshimi.core.providers.configured import CopilotProvider
from hoshimi.core.providers.copilot import (
    AUTO_MODEL_ID,
    free_usable,
    is_free_account,
    plan_sku,
    resolve_auto,
)

FREE_TOKEN = ("tid=abc;exp=1795703057;iat=1795616657;sku=free_limited_copilot;"
              "proxy-ep=proxy.individual.githubcopilot.com;st=dotcom")
PAID_TOKEN = ("tid=abc;exp=1795703057;iat=1795616657;sku=max_monthly_subscriber_quota;"
              "proxy-ep=proxy.individual.githubcopilot.com;st=dotcom")


def _row(model_id: str, *, input_price: float, output_price: float, restricted: list[str] | None = None,
         kind: str = "chat", policy: str | None = None) -> dict:
    """One catalogue row, in the shape Copilot answers with."""
    billing: dict = {"token_prices": {"batch_size": 1000000,
                                      "default": {"cache_price": 0, "cache_write_price": 0,
                                                  "input_price": input_price, "output_price": output_price}}}
    if restricted is not None:
        billing["restricted_to"] = restricted
    row: dict = {"id": model_id, "name": model_id, "vendor": "OpenAI", "model_picker_enabled": False,
                 "supported_endpoints": ["/chat/completions"], "billing": billing,
                 "capabilities": {"type": kind, "limits": {"max_context_window_tokens": 128000}}}
    if policy is not None:
        row["policy"] = {"state": policy, "terms": ""}
    return row


# The rows of a real free account's listing, with the verdict each one
# earned at the wire: the priced ones and the plan-restricted ones are
# refused with ``model_not_supported``, the zero-priced ones answer.
REAL_LISTING = [
    _row("gpt-4o", input_price=0, output_price=0),
    _row("gpt-4o-mini", input_price=0, output_price=0),
    _row("gpt-4.1", input_price=0, output_price=0, policy="enabled"),
    _row("gpt-4o-2024-11-20", input_price=0, output_price=0),
    _row("gpt-3.5-turbo-0613", input_price=0, output_price=0),
    _row("gpt-5.4-nano", input_price=0, output_price=0,
         restricted=["free", "edu", "pro", "pro_plus", "business", "enterprise", "max"]),
    _row("copilot-search-a", input_price=0, output_price=0,
         restricted=["free", "edu", "pro", "pro_plus", "individual_trial", "max"]),
    _row("claude-opus-5", input_price=500, output_price=2500,
         restricted=["pro_plus", "business", "enterprise", "max"], policy="disabled"),
    _row("claude-haiku-4.5", input_price=100, output_price=500, policy="enabled"),
    _row("gpt-5.6-luna", input_price=20, output_price=120,
         restricted=["free", "edu", "pro", "pro_plus", "business", "enterprise", "max"], policy="enabled"),
    _row("mai-code-2-flash", input_price=10, output_price=50,
         restricted=["free", "edu", "pro", "pro_plus", "max", "business", "enterprise"], policy="enabled"),
    _row("kimi-k3-copilot", input_price=300, output_price=1500,
         restricted=["free", "pro", "pro_plus", "max"], policy="enabled"),
    _row("gemini-4-argon", input_price=200, output_price=1000,
         restricted=["pro_plus", "business", "enterprise", "max"], policy="disabled"),
    _row("text-embedding-3-small", input_price=0, output_price=0, kind="embeddings"),
    _row("gpt-6-luna-decisions", input_price=0, output_price=0, kind="decisions"),
]

WORKING = {"gpt-4o", "gpt-4o-mini", "gpt-4.1", "gpt-4o-2024-11-20", "gpt-3.5-turbo-0613",
           "gpt-5.4-nano", "copilot-search-a"}


def _free_account() -> Account:
    return Account({"id": "copilot-free", "access_token": FREE_TOKEN,
                    "base_url": "https://api.example.githubcopilot.com"}, provider_id="github-copilot")


def _listing_client(rows: list[dict]) -> httpx.Client:
    def handle(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/models"
        return httpx.Response(200, json={"data": rows})
    return httpx.Client(transport=httpx.MockTransport(handle))


def test_plan_sku_is_read_from_the_minted_token():
    assert plan_sku(FREE_TOKEN) == "free_limited_copilot"
    assert plan_sku(PAID_TOKEN) == "max_monthly_subscriber_quota"
    assert plan_sku("tid=abc;exp=1") == ""


def test_the_free_tier_is_recognised_from_the_token_and_the_login_record():
    assert is_free_account(_free_account())
    assert not is_free_account(Account({"access_token": PAID_TOKEN}, provider_id="github-copilot"))
    # The login records the SKU, so the tier survives a token rotation.
    assert is_free_account(Account({"copilot_sku": "free_limited_copilot"}, provider_id="github-copilot"))
    assert not is_free_account(Account({"access_token": "plain-api-key"}, provider_id="github-copilot"))


def test_the_free_rule_keeps_exactly_the_models_that_answer():
    """The rule is only worth having if it matches the wire, model by model."""
    kept = {row["id"] for row in REAL_LISTING if free_usable(row)}
    assert kept == WORKING
    # A zero-priced row the plan excludes is still refused.
    assert not free_usable(_row("gpt-3.5-turbo", input_price=0, output_price=0,
                                 restricted=["pro", "pro_plus", "max", "business", "enterprise"]))
    # A row priced for the account's tier is usable however the plan reads.
    assert free_usable(_row("gpt-4o", input_price=0, output_price=0,
                             restricted=["free", "pro", "business"]))
    # An embedding model is not a chat model.
    assert not free_usable(_row("text-embedding-3-small", input_price=0, output_price=0, kind="embeddings"))


def test_a_free_account_is_offered_only_what_it_can_run():
    """The free lane's listing must not offer the models it cannot use."""
    with _listing_client(REAL_LISTING) as client:
        lane = CopilotProvider(client, [_free_account()])
        models = lane.models()
    assert "claude-opus-5" not in models
    assert "gpt-5.6-luna" not in models
    assert "mai-code-2-flash" not in models
    assert "text-embedding-3-small" not in models
    assert WORKING <= set(models)


def test_a_paid_account_keeps_the_models_it_is_billed_for():
    """A paid plan may run what it pays for, so the free gate does not apply."""
    paid = Account({"id": "copilot-paid", "access_token": PAID_TOKEN,
                    "base_url": "https://api.example.githubcopilot.com"}, provider_id="github-copilot")
    with _listing_client(REAL_LISTING) as client:
        lane = CopilotProvider(client, [paid])
        models = lane.models()
    assert "claude-opus-5" in models
    assert "mai-code-2-flash" in models
    # The embeddings row is not a chat model on any plan.
    assert "text-embedding-3-small" not in models
    # The Auto route is for the account that needs it; a paid one names its model.
    assert AUTO_MODEL_ID not in models


def test_the_auto_route_is_offered_to_a_free_account():
    with _listing_client(REAL_LISTING) as client:
        lane = CopilotProvider(client, [_free_account()])
        models = lane.models()
    assert AUTO_MODEL_ID in models
    assert lane.specs()[AUTO_MODEL_ID].wire == "responses"


def test_a_free_account_does_not_hide_a_paid_accounts_models():
    """The gate is per account: one account's plan is not another's."""
    paid = Account({"id": "copilot-paid", "access_token": PAID_TOKEN,
                    "base_url": "https://api.example.githubcopilot.com"}, provider_id="github-copilot")
    free = _free_account()
    with _listing_client(REAL_LISTING) as client:
        lane = CopilotProvider(client, [free, paid])
        models = lane.models()
    # The paid account's catalogue keeps the models it may run.
    assert "claude-opus-5" in models
    # The free account's catalogue does not gain them.
    assert "claude-opus-5" not in lane._account_rows[free.id]
    assert "claude-opus-5" in lane._account_rows[paid.id]


def test_the_auto_route_is_routable_to_a_free_account():
    """The route is not a catalogue row, so the pool must still offer it."""
    free = _free_account()
    paid = Account({"id": "copilot-paid", "access_token": PAID_TOKEN,
                    "base_url": "https://api.example.githubcopilot.com"}, provider_id="github-copilot")
    with _listing_client(REAL_LISTING) as client:
        lane = CopilotProvider(client, [free, paid])
        lane.models()
        assert lane._stream_candidates(AUTO_MODEL_ID) == [free]
        # A model both plans can run is offered to both.
        assert lane._stream_candidates("gpt-4o") == [free, paid]


def test_the_auto_route_asks_copilot_and_keeps_the_session_token():
    """Auto picks a model the account may run, and the token is what lets it run."""
    seen: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path == "/auto":
            assert request.headers["Copilot-Integration-Id"] == "copilot-developer-cli"
            return httpx.Response(200, json={"session_token": "session-token",
                                             "selected_model": {"id": "mai-code-2-flash"},
                                             "expires_at": 1795704648})
        if request.url.path == "/responses":
            assert request.headers["Copilot-Session-Token"] == "session-token"
            body = request.read().decode()
            assert '"model":"mai-code-2-flash"' in body.replace(" ", "")
            return httpx.Response(200, text='data: {"type":"response.output_text.delta","delta":"hi"}\n\n'
                                          'data: {"type":"response.completed","response":{"status":"completed"}}\n\n')
        return httpx.Response(200, json={"data": REAL_LISTING})

    account = _free_account()
    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        lane = CopilotProvider(client, [account])
        chunks = list(lane.stream_raw(account, AUTO_MODEL_ID,
                                      {"messages": [{"role": "user", "content": "hello"}]}))
    assert "".join(str(chunk.get("text") or "") for chunk in chunks) == "hi"
    assert [request.url.path for request in seen] == ["/auto", "/responses"]


def test_the_auto_route_is_reused_within_a_conversation():
    """One conversation keeps one session; a round trip per message would be waste."""
    auto_calls: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/auto":
            auto_calls.append(request)
            return httpx.Response(200, json={"session_token": "session-token",
                                             "selected_model": {"id": "mai-code-2-flash"}})
        if request.url.path == "/responses":
            return httpx.Response(200, text='data: {"type":"response.output_text.delta","delta":"ok"}\n\n')
        return httpx.Response(200, json={"data": REAL_LISTING})

    account = _free_account()
    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        lane = CopilotProvider(client, [account])
        body = {"messages": [{"role": "user", "content": "hello"}], "session_id": "conv-1"}
        list(lane.stream_raw(account, AUTO_MODEL_ID, body))
        list(lane.stream_raw(account, AUTO_MODEL_ID, body))
        # A different conversation does not inherit the session.
        list(lane.stream_raw(account, AUTO_MODEL_ID, {**body, "session_id": "conv-2"}))
    assert len(auto_calls) == 2


def test_a_refused_auto_route_says_so_instead_of_failing_obscurely():
    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/auto":
            return httpx.Response(403, json={"error": "no auto routing for this account"})
        return httpx.Response(200, json={"data": REAL_LISTING})

    account = _free_account()
    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        lane = CopilotProvider(client, [account])
        with pytest.raises(Exception) as failure:
            list(lane.stream_raw(account, AUTO_MODEL_ID, {"messages": [{"role": "user", "content": "hi"}]}))
    assert "Auto" in str(failure.value)


def test_resolve_auto_reports_a_missing_model_rather_than_guessing():
    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"session_token": "session-token"})
    account = _free_account()
    with httpx.Client(transport=httpx.MockTransport(handle)) as client, pytest.raises(Exception) as failure:
        resolve_auto(client, account, "https://api.example.githubcopilot.com", "hi", False)
    assert "no model" in str(failure.value)
