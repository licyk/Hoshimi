"""The HTTP surface: OpenAI-compatible endpoints, the management API and the security layer."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from hoshimi.core.chunks import aggregate_chat, emit_chat_chunks
from tests.conftest import FakeProvider, make_services


def test_health_and_models(client: Any) -> None:
    health = client.get("/health")
    assert health.status_code == 200
    assert health.json() == {"status": "ok"}

    app_health = client.get("/api/v1/app/health")
    assert app_health.status_code == 200
    assert app_health.json()["status"] == "ok"

    models = client.get("/v1/models")
    assert models.status_code == 200
    assert {item["id"] for item in models.json()["data"]} == {"fake-1", "fake-2"}


def test_app_meta_reports_the_server(services: Any) -> None:
    from fastapi.testclient import TestClient

    from hoshimi.api.app import create_app

    with TestClient(create_app(services, serve_ui=False), base_url="http://localhost") as client:
        meta = client.get("/api/v1/app/meta")
    assert meta.status_code == 200
    payload = meta.json()
    assert payload["auth_required"] is False
    assert payload["providers"] == 1
    assert payload["accounts"] == 1
    assert payload["api_prefix"] == ""
    assert payload["config_path"] == str(services.data_dir / "config.json")
    assert not services.config_path.exists()


def test_chat_completion_streaming_and_json(client: Any) -> None:
    with client.stream(
        "POST",
        "/v1/chat/completions",
        json={"model": "fake-1", "stream": True, "messages": [{"role": "user", "content": "hi"}]},
    ) as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        payloads = [
            json.loads(line[6:])
            for line in (text for text in response.iter_lines() if text.startswith("data: "))
            if line != "data: [DONE]"
        ]
    deltas = [chunk["choices"][0]["delta"].get("content") for chunk in payloads if chunk.get("choices")]
    text = "".join(part for part in deltas if part)
    assert text == "echo:fake-1:1"

    # Non-streaming path aggregates the same stream.
    response = client.post(
        "/v1/chat/completions",
        json={"model": "fake-1", "stream": False, "messages": [{"role": "user", "content": "hi"}]},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["choices"][0]["message"]["content"] == "echo:fake-1:1"
    assert payload["usage"]["total_tokens"] == 3


def test_responses_endpoint(client: Any) -> None:
    with client.stream(
        "POST",
        "/v1/responses",
        json={"model": "fake-1", "stream": True, "input": "hello"},
    ) as response:
        assert response.status_code == 200
        events = [
            json.loads(line[6:])
            for line in response.iter_lines()
            if line.startswith("data: ") and line != "data: [DONE]"
        ]
    assert events[0]["type"] == "response.created"
    assert events[-1]["type"] == "response.completed"
    output = events[-1]["response"]["output"]
    assert output[-1]["content"][0]["text"] == "echo:fake-1:1"

    response = client.post(
        "/v1/responses",
        json={"model": "fake-1", "stream": False, "input": "hello"},
    )
    assert response.status_code == 200
    assert response.json()["status"] == "completed"


def test_access_keys_enforced_except_public_probes(services: Any) -> None:
    from fastapi.testclient import TestClient

    from hoshimi.api.app import create_app

    services.settings.access_token = "secret"
    services.settings.model_api_key = "models"
    with TestClient(create_app(services, serve_ui=False), base_url="http://localhost") as client:
        # Public probes work without a key.
        assert client.get("/health").status_code == 200
        assert client.get("/api/v1/app/health").status_code == 200
        # Everything else needs the bearer token.
        assert client.get("/v1/models").status_code == 401
        assert client.get("/api/v1/app/meta").status_code == 401
        ok = client.get("/v1/models", headers={"Authorization": "Bearer models"})
        assert ok.status_code == 200


def test_unknown_model_returns_openai_shaped_404() -> None:
    from fastapi.testclient import TestClient

    from hoshimi.api.app import create_app

    # Two providers: a bare unknown name must not fall through to either of them.
    with TestClient(create_app(make_services(FakeProvider()), serve_ui=False), base_url="http://localhost") as client:
        response = client.post(
            "/v1/chat/completions",
            json={"model": "definitely-missing", "messages": [{"role": "user", "content": "x"}]},
        )
    assert response.status_code == 404
    error = response.json()["error"]
    assert error["type"] == "invalid_request_error"
    assert "definitely-missing" in error["message"]


def test_settings_roundtrip(client: Any, services: Any, tmp_path: Path) -> None:
    current = client.get("/api/v1/settings")
    assert current.status_code == 200
    assert "ofm_home" not in current.json()
    assert current.json()["server"]["port"] == 0
    assert "api_key" not in current.json()["server"]
    assert current.json()["config_path"] == str(services.data_dir / "config.json")
    assert not services.config_path.exists()

    patched = client.patch("/api/v1/settings", json={"server": {"default_model": "fake-1"}})
    assert patched.status_code == 200
    assert patched.json()["server"]["default_model"] == "fake-1"
    assert services.settings.default_model == "fake-1"
    assert services.config_path.is_file()
    from hoshimi.core.config import load_settings

    assert load_settings().default_model == "fake-1"

    rejected = client.patch("/api/v1/settings", json={"server": {"port": -1}})
    assert rejected.status_code == 400
    assert rejected.json()["code"] == "invalid_input"

    # The shapes the web UI patches: the config file's own nesting, not the flattened view.
    secured = client.patch("/api/v1/settings", json={"security": {"allow_cidrs": ["10.0.0.0/8"]}})
    assert secured.status_code == 200
    assert secured.json()["server"]["allow_cidrs"] == ["10.0.0.0/8"]
    assert services.settings.allow_cidrs == ["10.0.0.0/8"]

    keyed = client.patch("/api/v1/settings", json={"server": {"access_token": "sekret"}})
    assert keyed.status_code == 200
    assert keyed.json()["server"]["access_token_configured"] is True
    assert "api_key" not in keyed.json()["server"]  # the key itself never leaves the server
    cleared = client.patch(
        "/api/v1/settings",
        json={"server": {"access_token": None}},
        headers={"Authorization": "Bearer sekret"},
    )
    assert cleared.status_code == 200
    assert cleared.json()["server"]["access_token_configured"] is False
    assert services.settings.access_token is None


def test_an_account_added_through_the_api_is_live_immediately(client: Any) -> None:
    """The web UI's "add account" saves through this endpoint and expects it to work.

    Saving to the file alone would leave the running pool unchanged, so the
    account the operator just typed in would not exist until the process
    restarted — and the channels page would be showing an account the gateway
    cannot route to.
    """
    before = {row["id"]: row["account_ids"] for row in client.get("/api/v1/providers").json()}
    assert before["fake"] == ["fake-0"]

    saved = client.patch("/api/v1/settings", json={"providers": {"fake": {"accounts": [{"id": "second", "access_token": "t"}]}}})
    assert saved.status_code == 200

    after = {row["id"]: row["account_ids"] for row in client.get("/api/v1/providers").json()}
    assert after["fake"] == ["second"]


def test_a_save_that_touches_nothing_rebuilds_nothing(client: Any, services: Any) -> None:
    """A port change must not drop what only memory knows about the running lanes."""
    provider = services.registry.providers["fake"]
    provider.cooler.hold("fake", "fake-0", 3_600_000.0, reason="rate_limited")

    saved = client.patch("/api/v1/settings", json={"server": {"port": 12345}})
    assert saved.status_code == 200

    assert services.registry.providers["fake"] is provider
    assert services.registry.providers["fake"].cooler.blocked("fake", "fake-0") is True


def test_deleting_an_account_added_through_the_api(client: Any) -> None:
    """Add and forget round-trip: the pool follows both, and the file agrees."""
    client.patch("/api/v1/settings", json={"providers": {"fake": {"accounts": [{"id": "second", "access_token": "t"}]}}})
    listed = {row["id"]: row["account_ids"] for row in client.get("/api/v1/providers").json()}
    assert listed["fake"] == ["second"]

    deleted = client.delete("/api/v1/providers/fake/accounts/second")
    assert deleted.status_code == 200
    assert deleted.json()["removed_from_config"] is True

    listed = {row["id"]: row["account_ids"] for row in client.get("/api/v1/providers").json()}
    assert listed["fake"] == []


def test_providers_listing(client: Any) -> None:
    response = client.get("/api/v1/providers")
    assert response.status_code == 200
    payload = response.json()
    assert payload[0]["id"] == "fake"
    assert payload[0]["accounts"] == 1
    assert payload[0]["models"] is None  # Metadata never starts catalogue discovery.


def test_delete_account_removes_from_pool_and_config(client: Any, services: Any, tmp_path: Path) -> None:
    """The channels page's "forget" button: gone from the running pool and from the file."""
    # Give the fake provider a configured account so there is something to delete twice over.
    client.patch("/api/v1/settings", json={"providers": {"fake": {"accounts": [{"id": "fake-0", "access_token": "t"}]}}})
    listed = {row["id"]: row for row in client.get("/api/v1/providers").json()}
    assert "fake-0" in listed["fake"]["account_ids"]

    deleted = client.delete("/api/v1/providers/fake/accounts/fake-0")
    assert deleted.status_code == 200
    assert deleted.json()["removed_from_config"] is True
    listed = {row["id"]: row for row in client.get("/api/v1/providers").json()}
    assert listed["fake"]["account_ids"] == []
    assert listed["fake"]["accounts"] == 0
    config = json.loads(services.config_path.read_text(encoding="utf-8"))
    # An empty account list is written as no `accounts` key at all (settings_to_dict skips it).
    assert config["providers"]["fake"].get("accounts", []) == []

    # A second delete has nothing left to remove and says so.
    again = client.delete("/api/v1/providers/fake/accounts/fake-0")
    assert again.status_code == 404


def test_delete_account_drops_an_imported_one_without_a_config_entry() -> None:
    """An account the config never named lives only in the running pool; forgetting it still works.

    `fake0` is a lane `make_services` puts in the pool without declaring it —
    the shape an OFM import actually has. `fake` itself is declared now, so it
    is the wrong fixture for this: forgetting a declared account *should* reach
    the file, which is the other test.
    """
    services = make_services(FakeProvider())
    assert "fake0" not in services.settings.providers

    from fastapi.testclient import TestClient

    from hoshimi.api.app import create_app

    with TestClient(create_app(services, serve_ui=False), base_url="http://localhost") as client:
        listed = {row["id"]: row for row in client.get("/api/v1/providers").json()}
        assert "fake-0" in listed["fake0"]["account_ids"]

        deleted = client.delete("/api/v1/providers/fake0/accounts/fake-0")
        assert deleted.status_code == 200
        assert deleted.json()["removed_from_config"] is False

        listed = {row["id"]: row for row in client.get("/api/v1/providers").json()}
        assert listed["fake0"]["account_ids"] == []


def test_stations_endpoint_offers_the_table_a_form_can_choose_from(client: Any) -> None:
    """The channels page's dropdown and `hoshimi stations` print the same published table."""
    from hoshimi.core.presets import PRESETS

    response = client.get("/api/v1/stations")
    assert response.status_code == 200
    payload = response.json()
    assert [row["id"] for row in payload] == [preset.id for preset in PRESETS]
    assert len(payload) == 22
    # Reference data and nothing more: no credential comes attached to the table.
    assert set(payload[0]) == {"id", "display_name", "base_url", "key_env", "catalog_keyless"}
    assert all(row["base_url"].startswith("https://") for row in payload)
    # Six publish a catalogue without a credential — a fact about browsing only,
    # since none of them answers a *chat* call without one.
    assert sum(1 for row in payload if row["catalog_keyless"]) == 6


def test_adding_an_account_appends_and_reaches_the_running_pool(client: Any, services: Any, tmp_path: Path) -> None:
    """The channels page's "add account": the file gains an entry and the pool has it at once.

    Appending matters as much as arriving. The settings a client can read carry
    account *counts* only, so an endpoint that replaced the list would be
    overwriting accounts it had never seen — and "add" taking away is a lie.
    """
    client.patch("/api/v1/settings", json={"providers": {"agg": {"accounts": [{"id": "kept", "preset": "bai", "api_key": "k0"}]}}})

    added = client.post("/api/v1/providers/agg/accounts", json={"account": {"preset": "llm7", "api_key": "sk-new"}})
    assert added.status_code == 200
    assert added.json() == {"provider": "agg", "account": "agg-1", "accounts": 2}

    listed = {row["id"]: row for row in client.get("/api/v1/providers").json()}
    assert listed["agg"]["account_ids"] == ["kept", "agg-1"]
    config = json.loads(services.config_path.read_text(encoding="utf-8"))
    assert [entry["id"] for entry in config["providers"]["agg"]["accounts"]] == ["kept", "agg-1"]


def test_the_lane_refuses_an_account_it_would_drop_at_build_time(client: Any, services: Any, tmp_path: Path) -> None:
    """A blob the pool discards while building must be refused, not reported as added.

    Otherwise the save announces an account that does not exist, which is
    exactly what the channels page would go on displaying.
    """

    no_key = client.post("/api/v1/providers/agg/accounts", json={"account": {"preset": "bai"}})
    assert no_key.status_code == 400
    assert no_key.json()["code"] == "invalid_input"
    assert "api_key" in no_key.json()["message"]

    no_station = client.post("/api/v1/providers/agg/accounts", json={"account": {"api_key": "sk"}})
    assert no_station.status_code == 400
    assert "preset" in no_station.json()["message"]

    # Neither refusal wrote a file, and neither let a lane into the pool.
    assert not services.config_path.exists()
    assert "agg" not in services.registry.providers


def test_adding_to_a_provider_that_does_not_exist_is_not_found(client: Any) -> None:
    response = client.post("/api/v1/providers/nope/accounts", json={"account": {"api_key": "k"}})
    assert response.status_code == 404
    assert response.json()["code"] == "not_found"


def test_adding_an_account_id_the_lane_already_uses_conflicts(client: Any, services: Any, tmp_path: Path) -> None:
    """Two accounts behind one name would make "forget" match whichever it reached first."""
    client.patch("/api/v1/settings", json={"providers": {"agg": {"accounts": [{"id": "kept", "preset": "bai", "api_key": "k"}]}}})

    conflict = client.post("/api/v1/providers/agg/accounts", json={"account": {"id": "kept", "preset": "bai", "api_key": "k2"}})
    assert conflict.status_code == 409
    assert conflict.json()["code"] == "conflict"

    listed = {row["id"]: row for row in client.get("/api/v1/providers").json()}
    assert listed["agg"]["account_ids"] == ["kept"]


def test_the_generated_id_avoids_the_names_the_pool_derives_itself(client: Any, services: Any, tmp_path: Path) -> None:
    """An entry with no `id` is named from its position, so that position is already taken.

    Handing out `agg-1` here would put two accounts behind one name, and the
    forget button would then delete the wrong one.
    """
    client.patch("/api/v1/settings", json={"providers": {"agg": {"accounts": [{"preset": "bai", "api_key": "k0"}, {"preset": "llm7", "api_key": "k1"}]}}})
    listed = {row["id"]: row for row in client.get("/api/v1/providers").json()}
    assert listed["agg"]["account_ids"] == ["agg-0", "agg-1"]

    added = client.post("/api/v1/providers/agg/accounts", json={"account": {"preset": "bai", "api_key": "k2"}})
    assert added.json()["account"] == "agg-2"
    listed = {row["id"]: row for row in client.get("/api/v1/providers").json()}
    assert listed["agg"]["account_ids"] == ["agg-0", "agg-1", "agg-2"]


def test_forgetting_an_account_the_pool_named_by_position(client: Any, services: Any, tmp_path: Path) -> None:
    """Add and forget answer "which account is this" the same way.

    The pool names a position-named entry from its index, but the file stores
    no id to match on — so forgetting on the file's terms answered "removed"
    while leaving the entry behind for the next rebuild to hand straight back.
    """
    client.patch("/api/v1/settings", json={"providers": {"agg": {"accounts": [{"preset": "bai", "api_key": "k0"}]}}})

    removed = client.delete("/api/v1/providers/agg/accounts/agg-0")
    assert removed.status_code == 200
    assert removed.json()["removed_from_config"] is True

    config = json.loads(services.config_path.read_text(encoding="utf-8"))
    assert config["providers"]["agg"].get("accounts", []) == []
    listed = {row["id"]: row for row in client.get("/api/v1/providers").json()}
    assert listed["agg"]["account_ids"] == []


def test_auto_flags_travel_through_settings(client: Any) -> None:
    """The channels page's switches read and write server.auto_refresh / server.auto_checkin."""
    current = client.get("/api/v1/settings").json()["server"]
    assert current["auto_refresh"] is True
    assert current["auto_checkin"] is True

    flipped = client.patch("/api/v1/settings", json={"server": {"auto_refresh": False, "auto_checkin": False}})
    assert flipped.status_code == 200
    assert flipped.json()["server"]["auto_refresh"] is False
    assert flipped.json()["server"]["auto_checkin"] is False


def test_credits_report_carries_the_checkin_state(client: Any, services: Any) -> None:
    """The channels page reads "today" from the balance round; no claim round is spent on it."""
    services.settings.auto_checkin = False
    body = client.get("/api/v1/credits").json()
    assert "checkin" in body
    assert body["checkin"]["auto_checkin"] is False
    assert body["checkin"]["last_date"] == ""


def test_credits_report_carries_the_accounts_the_pool_is_holding_back(client: Any, services: Any) -> None:
    """A lane down to half its accounts has to be explainable from the outside.

    Without this the hold is invisible: the caller sees a station failing on
    every turn and has no way to ask which account said no, or when it comes
    back. `reason` is the gateway's own name, never the station's body.
    """
    lane = next(iter(services.registry.providers.values()))
    lane.cooler.hold(lane.id, "an-account", 900.0, reason="policy_blocked")
    try:
        body = client.get("/api/v1/credits").json()
        rows = {(row["provider"], row["account"]): row for row in body["cooled"]}
        assert rows[(lane.id, "an-account")]["reason"] == "policy_blocked"
        assert rows[(lane.id, "an-account")]["seconds_left"] > 890
    finally:
        lane.cooler.clear(lane.id)


def test_credits_report_carries_passive_quota_read_off_serving_turns(client: Any) -> None:
    """The channels page can show what is left without spending a balance query."""
    from hoshimi.core.quota import QUOTA

    QUOTA.reset()
    QUOTA.observe("agg", 200, {"x-ratelimit-remaining": "3", "x-ratelimit-limit": "4"})
    try:
        body = client.get("/api/v1/credits").json()
        rows = {row["provider"]: row for row in body["passive"]}
        assert rows["agg"]["percent"] == 75
        # A header *name*, never a value: this report has to be safe to read.
        assert rows["agg"]["source"] == "x-ratelimit-remaining"
        assert rows["agg"]["stale"] is False
        assert rows["agg"]["counters"]["responses"] == 1
        assert rows["agg"]["counters"]["quota_header_drift"] == 0
    finally:
        QUOTA.reset()


def test_provider_listing_labels_the_lane_needing_authorization(client: Any) -> None:
    """The co-paid lane has no anonymous mode: it is listed so the UI can ask for one."""
    payload = {row["id"]: row for row in client.get("/api/v1/providers").json()}
    eac = payload["eac"]
    assert eac["enabled"] is False
    assert eac["requires_authorization"] is True
    assert eac["accounts"] == 0
    assert eac["display_name"] == "EAC (co-paid)"


def test_aggregate_helper_used_by_non_stream_path() -> None:
    provider = FakeProvider()
    normalized = provider.stream_raw(provider.accounts[0], "fake-1", {"messages": [{"role": "user", "content": "x"}]})
    message, finish, usage = aggregate_chat(emit_chat_chunks(normalized, model="fake-1", completion_id="c", created=1))
    assert message["content"] == "echo:fake-1:1"
    assert finish == "stop"
    assert usage is not None and usage["total_tokens"] == 3


def test_model_catalog_reports_capability_specs(client: Any) -> None:
    response = client.get("/api/v1/models")
    assert response.status_code == 200
    payload = response.json()
    assert [level["id"] for level in payload["levels"]] == ["light", "balanced", "deep"]
    rows = {row["id"]: row for row in payload["data"]}
    assert set(rows) == {"fake-1", "fake-2"}
    # An account channel with no capability table claims nothing it cannot prove.
    assert rows["fake-1"]["name"] == "Fake 1"
    assert rows["fake-1"]["reasoning"] is False
    assert rows["fake-1"]["efforts"] == []
    assert rows["fake-1"]["wire"] == "chat"


def test_the_free_mark_reaches_both_catalogues(client: Any) -> None:
    """The same fact in the two shapes a client may ask for.

    `fake-2` is the other half of the assertion: a lane with no evidence about
    a price says so, rather than claiming the model is paid. OpenAI clients
    ignore the extra key on `/v1/models`; a client that reads it gets the same
    verdict the management API gives.
    """
    listed = {row["id"]: row for row in client.get("/v1/models").json()["data"]}
    assert listed["fake-1"]["free"] is True
    assert listed["fake-2"]["free"] is None

    rows = {row["id"]: row for row in client.get("/api/v1/models").json()["data"]}
    assert rows["fake-1"]["free"] is True
    assert rows["fake-2"]["free"] is None


def test_a_banned_model_stays_listed_and_is_flagged(client: Any, services: Any) -> None:
    """The flag and the escape hatch are all an operator gets for an in-memory ban."""
    services.registry.dead_models.strike("fake", "fake-1", reason="HTTP 404")

    rows = {row["id"]: row for row in client.get("/api/v1/models").json()["data"]}
    assert rows["fake-1"]["unavailable"] is True
    assert rows["fake-2"]["unavailable"] is False
    # Still in the OpenAI catalogue: a client that cached the id must not be
    # told it never existed, only that it cannot be answered right now.
    listed = {item["id"] for item in client.get("/v1/models").json()["data"]}
    assert "fake-1" in listed

    reset = client.post("/api/v1/models/unavailable/reset")
    assert reset.status_code == 200
    assert reset.json() == {"cleared": 1}
    rows = {row["id"]: row for row in client.get("/api/v1/models").json()["data"]}
    assert rows["fake-1"]["unavailable"] is False


def test_probe_round_reports_verdicts_per_model(client: Any) -> None:
    started = client.post("/api/v1/probe", json={"models": ["fake-1"]})
    assert started.status_code == 200
    assert started.json()["started"] is True

    deadline = time.time() + 5
    payload: dict[str, Any] = {}
    while time.time() < deadline:
        payload = client.get("/api/v1/probe").json()
        # Wait for both, not just the verdict: a result is recorded before the
        # round's own bookkeeping lands, so breaking on the verdict alone reads
        # `running: True` and fails a round that was never wrong.
        if "fake-1" in payload["results"] and not payload["running"]:
            break
        time.sleep(0.02)
    assert payload["running"] is False
    assert payload["at"] is not None
    assert payload["results"]["fake-1"]["state"] == "available"
    assert payload["results"]["fake-1"]["latency_ms"] >= 0

    # The catalogue carries the verdict so the UI can group by it.
    rows = {row["id"]: row for row in client.get("/api/v1/models").json()["data"]}
    assert rows["fake-1"]["probe"]["state"] == "available"
    assert rows["fake-2"]["probe"] is None


def test_probe_reports_ids_no_catalogue_names() -> None:
    from fastapi.testclient import TestClient

    from hoshimi.api.app import create_app

    # Two providers, so a bare id cannot be passed through blindly.
    services = make_services(FakeProvider())
    with TestClient(create_app(services, serve_ui=False), base_url="http://localhost") as client:
        response = client.post("/api/v1/probe", json={"models": ["missing-model"]})
    assert response.status_code == 200
    assert response.json()["unresolved"] == ["missing-model"]


def test_stats_endpoint_counts_what_was_served() -> None:
    from datetime import datetime

    from fastapi.testclient import TestClient

    from hoshimi.api.app import create_app

    # Two providers: a bare unknown name is a refusal, and a refusal is usage too.
    with TestClient(create_app(make_services(FakeProvider()), serve_ui=False), base_url="http://localhost") as client:
        answered = client.post(
            "/v1/chat/completions",
            json={"model": "fake-1", "stream": False, "messages": [{"role": "user", "content": "hi"}]},
        )
        assert answered.status_code == 200
        refused = client.post(
            "/v1/chat/completions",
            json={"model": "nope-1", "stream": False, "messages": [{"role": "user", "content": "hi"}]},
        )
        assert refused.status_code == 404
        payload = client.get("/api/v1/stats").json()
        assert (payload["requests"], payload["failures"]) == (2, 1)
        rows = {row["model"]: row for row in payload["models"]}
        assert rows["fake-1"]["calls"] == 1
        assert rows["fake-1"]["failed"] == 0
        assert rows["nope-1"]["failed"] == 1
        assert payload["grand"] == {"input": 1, "output": 2, "reasoning": 0, "calls": 2, "failed": 1}

        day = payload["days"][-1]
        assert day["day"] == datetime.now().astimezone().date().isoformat()
        assert day["total"] == 3
        assert {row["model"] for row in day["models"]} == {"fake-1", "nope-1"}

        # The recent-call ring is what a "that answer vanished" report audits.
        assert payload["samples"][-1]["model"] == "nope-1"
        assert payload["samples"][-1]["ok"] is False
        assert payload["samples"][0]["origin"] == "chat"


def test_effort_sample_records_what_the_wire_carried() -> None:
    from fastapi.testclient import TestClient

    from hoshimi.api.app import create_app
    from tests.core.test_registry import SpecProvider

    services = make_services(SpecProvider("opencode", ["mimo-v2.6-flash-free"]))
    with TestClient(create_app(services, serve_ui=False), base_url="http://localhost") as client:
        asked = client.post(
            "/v1/chat/completions",
            json={
                "model": "mimo-v2.6-flash-free",
                "stream": True,
                "reasoning_effort": "high",
                "messages": [{"role": "user", "content": "x"}],
            },
        )
        assert asked.status_code == 200
        silent = client.post(
            "/v1/chat/completions",
            json={"model": "mimo-v2.6-flash-free", "stream": True, "messages": [{"role": "user", "content": "x"}]},
        )
        assert silent.status_code == 200
        samples = client.get("/api/v1/stats").json()["samples"]

    # The sample reports the rung the body carried; a caller who named none
    # sends none, and must not appear to have asked for the default.
    assert [row["effort"] for row in samples] == ["deep", ""]


def test_model_api_key_opens_v1_and_nothing_behind_it(services: Any) -> None:
    from fastapi.testclient import TestClient

    from hoshimi.api.app import create_app

    services.settings.access_token = "secret"
    services.settings.model_api_key = "lan-only"
    with TestClient(create_app(services, serve_ui=False), base_url="http://localhost") as client:
        # The model API key is a full credential on the inference surface...
        models = client.get("/v1/models", headers={"Authorization": "Bearer lan-only"})
        assert models.status_code == 200
        chat = client.post(
            "/v1/chat/completions",
            headers={"Authorization": "Bearer lan-only"},
            json={"model": "fake-1", "stream": False, "messages": [{"role": "user", "content": "hi"}]},
        )
        assert chat.status_code == 200
        # ...and no credential at all on the management API.
        assert client.get("/api/v1/settings", headers={"Authorization": "Bearer lan-only"}).status_code == 401
        assert client.get("/api/v1/app/meta", headers={"Authorization": "Bearer lan-only"}).status_code == 401

        # The tool access key does not unlock the separately protected model API.
        assert client.get("/v1/models", headers={"Authorization": "Bearer secret"}).status_code == 401
        assert client.get("/api/v1/settings", headers={"Authorization": "Bearer secret"}).status_code == 200
        assert client.get("/v1/models").status_code == 401
        assert client.get("/health").status_code == 200

        # The keys are never handed back by the API.
        payload = client.get("/api/v1/settings", headers={"Authorization": "Bearer secret"}).json()
        assert payload["server"]["model_api_key_configured"] is True
        assert "lan-only" not in client.get("/api/v1/settings", headers={"Authorization": "Bearer secret"}).text
        meta = client.get("/api/v1/app/meta", headers={"Authorization": "Bearer secret"}).json()
        assert meta["model_api_key_configured"] is True


def test_model_api_key_alone_still_keys_the_model_surface(services: Any) -> None:
    from fastapi.testclient import TestClient

    from hoshimi.api.app import create_app

    services.settings.model_api_key = "lan-only"
    with TestClient(create_app(services, serve_ui=False), base_url="http://localhost") as client:
        assert client.get("/v1/models").status_code == 401
        ok = client.get("/v1/models", headers={"Authorization": "Bearer lan-only"})
        assert ok.status_code == 200
        # No tool access key was set, so the management API is public.
        assert client.get("/api/v1/app/meta").status_code == 200
