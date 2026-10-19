"""Cloud routing contracts, native event streams, and classification isolation."""

import json
import struct
import zlib

import httpx
import pytest
from fastapi.testclient import TestClient

from hoshimi.api.app import create_app
from hoshimi.core.config import Settings
from hoshimi.core.context import Services
from hoshimi.core.provider_sources import PI_PROVIDERS
from hoshimi.core.providers import PROVIDER_CLASSES
from hoshimi.core.providers.base import Account
from hoshimi.core.providers.bedrock import BedrockProvider, bedrock_chunks, bedrock_payload
from hoshimi.core.providers.cloud import AzureProvider, CloudflareGatewayProvider, CloudflareWorkersProvider, GoogleProvider, VertexProvider
from hoshimi.core.providers.radius import RadiusProvider
from hoshimi.core.providers.typesafe import TypeSafeProvider
from hoshimi.core.registry import Registry


def test_all_upstream_providers_are_registered_without_brand_collisions():
    assert all(d.id in PROVIDER_CLASSES and d.coverage == "native" for d in PI_PROVIDERS)
    assert PROVIDER_CLASSES["minimax"] is not PROVIDER_CLASSES["minimax-api"]
    assert PROVIDER_CLASSES["gemini"] is not PROVIDER_CLASSES["google"]


def test_azure_deployment_mapping_preserves_responses_protocol():
    def handle(request):
        assert str(request.url) == "https://resource.openai.azure.com/openai/v1/responses"
        assert request.headers["api-key"] == "key" and "authorization" not in request.headers
        assert json.loads(request.content)["model"] == "deployment"
        return httpx.Response(200, text='data: {"type":"response.completed","response":{"status":"completed"}}\n\n')
    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        lane = AzureProvider(client, [Account({"api_key": "key", "resource": "resource", "deployments": {"public": "deployment"}})])
        assert list(lane.stream_raw(lane.accounts[0], "public", {"messages": [{"role": "user", "content": "x"}]}))[-1]["type"] == "finish"


@pytest.mark.parametrize("wire,suffix", [("chat", "compat/chat/completions"), ("messages", "anthropic/v1/messages"), ("responses", "openai/responses")])
def test_cloudflare_gateway_owns_auth_headers_and_wire_paths(wire, suffix):
    def handle(request):
        assert request.url.path == f"/v1/account/gateway/{suffix}"
        assert request.headers["cf-aig-authorization"] == "Bearer key"
        assert "authorization" not in request.headers and "x-api-key" not in request.headers
        return httpx.Response(200, text="data: [DONE]\n\n")
    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        lane = CloudflareGatewayProvider(client, [Account({"api_key": "key", "account_id": "account", "gateway_id": "gateway"})], {"model_protocols": {"m": wire}})
        list(lane.stream_raw(lane.accounts[0], "m", {"messages": [{"role": "user", "content": "x"}]}))


def test_cloudflare_workers_lists_model_names_instead_of_internal_ids():
    def handle(request):
        assert request.url.path == "/client/v4/accounts/account/ai/models/search"
        return httpx.Response(200, json={"result": [{"id": "internal", "name": "@cf/text", "task": {"name": "Text Generation"}}, {"name": "image", "task": {"name": "Text-to-Image"}}]})
    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        lane = CloudflareWorkersProvider(client, [Account({"api_key": "key", "account_id": "account"})])
        assert lane.models() == ["@cf/text"]


def test_google_directory_and_generation_use_developer_api_credentials():
    def handle(request):
        assert request.headers["x-goog-api-key"] == "key"
        if request.method == "GET":
            return httpx.Response(200, json={"models": [{"name": "models/gemini-test", "supportedGenerationMethods": ["generateContent"], "inputTokenLimit": 5000}, {"name": "models/embed", "supportedGenerationMethods": ["embedContent"]}]})
        assert request.url.path.endswith("/models/gemini-test:streamGenerateContent")
        payload = json.loads(request.content)
        assert "sessionId" not in payload and "project" not in payload
        assert "thinkingConfig" not in payload["generationConfig"]
        return httpx.Response(200, text='data: {"candidates":[{"content":{"parts":[{"text":"answer"}]},"finishReason":"STOP"}],"usageMetadata":{"promptTokenCount":2,"candidatesTokenCount":1,"totalTokenCount":3}}\n\n')
    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        lane = GoogleProvider(client, [Account({"api_key": "key"})])
        assert lane.models() == ["gemini-test"]
        assert lane.specs()["gemini-test"].context_window == 5000
        assert {"type": "text_delta", "text": "answer"} in list(lane.stream_raw(lane.accounts[0], "gemini-test", {"messages": [{"role": "user", "content": "x"}]}))


def test_vertex_adc_is_lazy_and_uses_project_routing(monkeypatch):
    google_auth = pytest.importorskip("google.auth")
    class Credentials:
        valid = False
        token = ""
        def refresh(self, request):
            self.token, self.valid = "access", True
    monkeypatch.setattr(google_auth, "default", lambda **kwargs: (Credentials(), "project"))
    def handle(request):
        assert request.headers["authorization"] == "Bearer access"
        assert "/projects/project/locations/global/publishers/google/models/gemini-test:" in request.url.path
        return httpx.Response(200, text='data: {"candidates":[{"content":{"parts":[{"text":"x"}]}}]}\n\n')
    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        lane = VertexProvider(client, [Account({"auth": "adc", "project": "project"})])
        list(lane.stream_raw(lane.accounts[0], "gemini-test", {"messages": [{"role": "user", "content": "x"}]}))


def test_bedrock_payload_matches_installed_sdk_schema():
    session = pytest.importorskip("botocore.session")
    validate = pytest.importorskip("botocore.validate")
    shape = session.get_session().get_service_model("bedrock-runtime").operation_model("ConverseStream").input_shape
    payload = bedrock_payload({"messages": [{"role": "user", "content": "x"}], "tools": [{"type": "function", "function": {"name": "read", "parameters": {"type": "object", "properties": {}}}}]}, "anthropic.model")
    validate.validate_parameters(payload, shape)


def test_bedrock_binary_bearer_stream_uses_account_key_without_environment_changes():
    pytest.importorskip("boto3")
    def frame(name, payload):
        value = name.encode()
        headers = b"\x0b:event-type\x07" + struct.pack("!H", len(value)) + value
        data = json.dumps(payload).encode()
        prelude = struct.pack("!II", 16 + len(headers) + len(data), len(headers))
        message = prelude + struct.pack("!I", zlib.crc32(prelude)) + headers + data
        return message + struct.pack("!I", zlib.crc32(message))
    stream = frame("contentBlockDelta", {"contentBlockIndex": 0, "delta": {"text": "answer"}}) + frame("messageStop", {"stopReason": "end_turn"})
    def handle(request):
        assert request.headers["authorization"] == "Bearer bedrock-key"
        assert request.url.path == "/model/anthropic.model/converse-stream"
        return httpx.Response(200, content=stream)
    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        lane = BedrockProvider(client, [Account({"api_key": "bedrock-key"})])
        chunks = list(lane.stream_raw(lane.accounts[0], "anthropic.model", {"messages": [{"role": "user", "content": "x"}]}))
        assert chunks[0] == {"type": "text_delta", "text": "answer"}
        assert chunks[-1] == {"type": "finish", "reason": "stop"}


def test_bedrock_streamed_tool_arguments_and_usage():
    chunks = list(bedrock_chunks([
        {"contentBlockStart": {"contentBlockIndex": 1, "start": {"toolUse": {"toolUseId": "t", "name": "read"}}}},
        {"contentBlockDelta": {"contentBlockIndex": 1, "delta": {"toolUse": {"input": "{}"}}}},
        {"messageStop": {"stopReason": "tool_use"}}, {"metadata": {"usage": {"inputTokens": 2, "outputTokens": 3, "totalTokens": 5}}},
    ]))
    assert chunks[1] == {"type": "tool_call_delta", "index": 1, "arguments": "{}"}
    assert chunks[2]["reason"] == "tool_calls"
    assert chunks[3]["usage"]["total_tokens"] == 5


def test_radius_config_pi_history_and_native_events():
    def handle(request):
        if request.method == "GET":
            assert request.url.path == "/v1/config"
            return httpx.Response(200, json={"baseUrl": "https://radius.pi.dev/inference", "models": [{"id": "vendor/model", "contextWindow": 64000}]})
        assert request.url.path == "/inference/messages"
        body = json.loads(request.content)
        assert body["context"]["messages"][0]["toolsAdded"][0]["name"] == "read"
        assert body["context"]["messages"][-1]["role"] == "user"
        return httpx.Response(200, text='data: {"type":"text_delta","contentIndex":0,"delta":"answer"}\n\ndata: {"type":"done","reason":"stop","usage":{"input":2,"output":1}}\n\n')
    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        lane = RadiusProvider(client, [Account({"api_key": "key"})])
        assert lane.models() == ["vendor/model"]
        chunks = list(lane.stream_raw(lane.accounts[0], "vendor/model", {"messages": [{"role": "user", "content": "x"}], "tools": [{"type": "function", "function": {"name": "read"}}]}))
        assert chunks[0]["text"] == "answer"


def test_typesafe_classifiers_are_discoverable_separately_from_chat(tmp_path):
    def handle(request):
        assert request.url.path == "/v1/systemone"
        assert json.loads(request.content)["questions"]["q"]["type"] == "noul"
        return httpx.Response(200, json={"answers": {"q": {"type": "noul", "noul": 0.8}}, "usage": {"input_tokens": 4, "output_tokens": 1}})
    with httpx.Client(transport=httpx.MockTransport(handle)) as upstream:
        lane = TypeSafeProvider(upstream, [Account({"api_key": "key"})])
        services = Services(settings=Settings(), registry=Registry({"typesafe": lane}))
        with TestClient(create_app(services, serve_ui=False), base_url="http://localhost") as client:
            assert client.get("/v1/models").json()["data"] == []
            assert client.get("/v1/classifiers").json()["data"][0]["id"] == "typesafe/jev-latest"
            response = client.post("/v1/classify", json={"model": "typesafe/jev-latest", "state": {"text": "x"}, "questions": {"q": {"type": "bool", "instructions": "relevant?", "criteria": {"true": "yes", "false": "no"}}}})
            assert response.status_code == 200
            assert response.json()["answers"]["q"] == {"type": "bool", "probability": 0.8}
