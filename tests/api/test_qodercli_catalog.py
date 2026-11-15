"""Qoder's first catalogue response must contain the live thinking menu."""

import json

import httpx
import pytest
from fastapi.testclient import TestClient

from hoshimi.api.app import create_app
from hoshimi.core.config import Settings
from hoshimi.core.context import Services
from hoshimi.core.providers.base import Account
from hoshimi.core.providers.qodercli import QoderCliCnProvider
from hoshimi.core.registry import Registry


@pytest.mark.parametrize("route", ["/v1/models", "/v1/models?limit=100", "/api/v1/models", "/api/v1/catalog/stream"])
def test_first_catalogue_reads_live_qoder_efforts(route):
    requests = []

    def upstream(request):
        if request.url.path.endswith("/model/list"):
            requests.append(request)
            return httpx.Response(200, json={"chat": [{
                "key": "dfmodel", "display_name": "DeepSeek-Flash", "enable": True,
                "is_reasoning": False,
                "thinking_config": {"disabled": {}, "enabled": {
                    "efforts": {"high": {"is_default": True}, "max": {}, "low": {}},
                    "is_default": True,
                }},
            }]})
        return httpx.Response(404)

    http = httpx.Client(transport=httpx.MockTransport(upstream))
    account = Account({"access_token": "fixture", "uid": "user", "machine_id": "machine", "expires_at": 9_999_999_999_999})
    provider = QoderCliCnProvider(http, [account])
    services = Services(settings=Settings(check_updates=False), registry=Registry({provider.id: provider}, client=http))
    with TestClient(create_app(services, serve_ui=False), base_url="http://localhost") as client:
        for _ in range(2):
            response = client.get(route)
            assert response.status_code == 200
            if route.endswith("/stream"):
                events = [json.loads(line.removeprefix("data: ")) for line in response.text.splitlines() if line.startswith("data:")]
                rows = next(event["provider"]["data"] for event in events if event["type"] == "provider")
            else:
                rows = response.json()["data"]
            row = next(row for row in rows if row["id"] == "DeepSeek-Flash")
            assert row["reasoning"] is True
            assert [effort["id"] for effort in row["efforts"]] == ["low", "high", "max", "none"]
            assert row["effort_default"] == "high"
            assert [effort["id"] for effort in row["efforts"] if effort["is_default"]] == ["high"]
        assert len(requests) == 1
