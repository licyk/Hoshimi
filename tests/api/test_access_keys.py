"""Tool access and model API credentials remain independent across updates and mounts."""

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from hoshimi.api.app import create_app
from hoshimi.core.config import Settings, settings_from_dict, settings_to_dict


@pytest.mark.parametrize("prefix", ["", "/gateway", "/mounted/gateway"])
def test_keys_have_independent_scopes_and_playground_uses_tool_access(services, prefix):
    services.settings.access_token = "tool-key"
    services.settings.model_api_key = "model-key"
    app = create_app(services, api_prefix="/gateway" if prefix else "", serve_ui=False)
    if prefix.startswith("/mounted"):
        host = FastAPI()
        host.mount("/mounted", app)
        app = host
    with TestClient(app, base_url="http://localhost") as client:
        tool = {"Authorization": "Bearer tool-key"}
        model = {"Authorization": "Bearer model-key"}
        for path in ("/api/v1/settings", "/api/v1/app/meta", "/api/v1/playground/models", "/openapi.json", "/docs"):
            assert client.get(prefix + path, headers=tool).status_code == 200
            assert client.get(prefix + path, headers=model).status_code == 401
        assert client.get(prefix + "/v1/models", headers=model).status_code == 200
        assert client.get(prefix + "/v1/models", headers=tool).status_code == 401
        assert client.get(prefix + "/v1/models", headers={"x-api-key": "model-key"}).status_code == 200
        assert client.get(prefix + "/api/v1/settings", headers={"x-api-key": "tool-key"}).status_code == 401
        body = {"model": "fake-1", "messages": [{"role": "user", "content": "hi"}], "stream": False}
        assert client.post(prefix + "/v1/chat/completions", json=body, headers=model).status_code == 200
        assert client.post(prefix + "/api/v1/playground/chat/completions", json=body, headers=model).status_code == 401
        reply = client.post(prefix + "/api/v1/playground/chat/completions", json=body, headers=tool)
        assert reply.status_code == 200 and reply.json()["choices"][0]["message"]["content"]
        streamed = client.post(prefix + "/api/v1/playground/chat/completions", json={**body, "stream": True}, headers=tool)
        assert streamed.status_code == 200 and "data: [DONE]" in streamed.text
        assert client.get(prefix + "/api/v1/app/health").json()["auth_required"] is True


@pytest.mark.parametrize("tool_key,model_key,tool_status,model_status", [
    (None, None, 200, 200), ("tool", None, 401, 200), (None, "model", 200, 401), ("tool", "model", 401, 401),
])
def test_unset_key_opens_only_its_own_surface(services, tool_key, model_key, tool_status, model_status):
    services.settings.access_token = tool_key
    services.settings.model_api_key = model_key
    with TestClient(create_app(services, serve_ui=False), base_url="http://localhost") as client:
        assert client.get("/api/v1/settings").status_code == tool_status
        assert client.get("/v1/models").status_code == model_status


def test_key_rotation_clear_and_rejected_save_are_atomic(services):
    services.settings.access_token = "tool-old"
    services.settings.model_api_key = "model-old"
    with TestClient(create_app(services, serve_ui=False), base_url="http://localhost") as client:
        old = {"Authorization": "Bearer tool-old"}
        response = client.patch("/api/v1/settings", headers=old, json={"server": {"access_token": "tool-new"}})
        assert response.status_code == 200
        assert "tool-new" not in response.text and "model-old" not in response.text
        assert response.json()["server"]["access_token_configured"] is True
        assert response.json()["server"]["model_api_key_configured"] is True
        assert client.get("/api/v1/settings", headers=old).status_code == 401
        new = {"Authorization": "Bearer tool-new"}
        rejected = client.patch("/api/v1/settings", headers=new, json={"server": {"access_token": "must-not-apply", "port": -1}})
        assert rejected.status_code == 400
        assert services.settings.access_token == "tool-new"
        rotated = client.patch("/api/v1/settings", headers=new, json={"server": {"model_api_key": "model-new"}})
        assert rotated.status_code == 200
        assert client.get("/v1/models", headers={"Authorization": "Bearer model-old"}).status_code == 401
        assert client.get("/v1/models", headers={"Authorization": "Bearer model-new"}).status_code == 200
        assert client.patch("/api/v1/settings", headers=new, json={"server": {"access_token": None}}).status_code == 200
        assert client.get("/api/v1/settings").status_code == 200
        assert client.get("/v1/models").status_code == 401
        assert client.patch("/api/v1/settings", json={"server": {"model_api_key": None}}).status_code == 200
        assert client.get("/v1/models").status_code == 200
    saved = json.loads(services.config_path.read_text())
    assert "api_key" not in saved["server"] and "relay_key" not in saved["server"]


def test_config_uses_only_the_new_credential_fields():
    data = {"server": {"access_token": " tool ", "model_api_key": " model "}}
    settings = settings_from_dict(data)
    assert settings.access_token == "tool" and settings.model_api_key == "model"
    stored = settings_to_dict(settings)
    assert stored["server"]["access_token"] == "tool" and stored["server"]["model_api_key"] == "model"
    cleared = settings_from_dict({"server": {"access_token": None, "model_api_key": ""}}, settings)
    assert cleared.access_token is None and cleared.model_api_key is None
    assert not hasattr(Settings(), "api_key") and not hasattr(Settings(), "relay_key")


@pytest.mark.parametrize("name", ["access_token", "model_api_key"])
def test_config_refuses_non_string_credentials(name):
    with pytest.raises(ValueError, match=f"server.{name}"):
        settings_from_dict({"server": {name: 123}})
