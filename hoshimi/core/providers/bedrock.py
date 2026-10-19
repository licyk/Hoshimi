"""Bedrock ConverseStream using optional AWS SDK or account-local bearer auth."""

from __future__ import annotations

import base64
import json
from collections.abc import Iterable, Iterator
from typing import Any
from urllib.parse import quote

import httpx

from hoshimi.core.errors import ValidationError
from hoshimi.core.providers.base import Account, ProviderError
from hoshimi.core.providers.keyed import KeyedProvider, account_token
from hoshimi.core.providers.openai_compat import raise_for_status
from hoshimi.core.quota import observe_response
from hoshimi.core.translate.anthropic import build_anthropic_body


def bedrock_payload(body: dict[str, Any], model: str, *, binary_images: bool = True) -> dict[str, Any]:
    source = build_anthropic_body(body, model=model)

    def block(item: dict[str, Any]) -> dict[str, Any]:
        kind = item.get("type")
        if kind == "tool_use":
            return {"toolUse": {"toolUseId": item["id"], "name": item["name"], "input": item["input"]}}
        if kind == "tool_result":
            content = item.get("content")
            parts = [block(part) for part in content if isinstance(part, dict)] if isinstance(content, list) else [{"text": str(content or "")}]
            return {"toolResult": {"toolUseId": item["tool_use_id"], "content": parts, "status": "error" if item.get("is_error") else "success"}}
        if kind == "image":
            image = item["source"]
            data = base64.b64decode(image["data"]) if binary_images else image["data"]
            return {"image": {"format": image["media_type"].split("/")[-1], "source": {"bytes": data}}}
        return {"text": str(item.get("text") or "")}

    payload: dict[str, Any] = {"modelId": model, "messages": [{"role": message["role"], "content": [block(item) for item in message["content"]]} for message in source["messages"]]}
    histories = iter(message for message in body.get("messages", []) if message.get("role") == "assistant" and (message.get("content") or message.get("tool_calls")))
    for message in payload["messages"]:
        if message["role"] == "assistant":
            original = next(histories, {})
            for detail in original.get("reasoning_details", []):
                if isinstance(detail, dict) and detail.get("type") == "bedrock_reasoning":
                    message["content"].insert(0, {"reasoningContent": {"reasoningText": {"text": detail["text"], "signature": detail["signature"]}}})
    if source.get("system"):
        payload["system"] = [{"text": source["system"]}]
    config = {"maxTokens": source["max_tokens"]}
    for original, key in (("temperature", "temperature"), ("top_p", "topP"), ("stop_sequences", "stopSequences")):
        if original in source:
            config[key] = source[original]
    payload["inferenceConfig"] = config
    if source.get("tools"):
        tool_config: dict[str, Any] = {"tools": [{"toolSpec": {"name": tool["name"], **({"description": tool["description"]} if tool["description"] else {}), "inputSchema": {"json": tool["input_schema"]}}} for tool in source["tools"]]}
        payload["toolConfig"] = tool_config
        choice = source.get("tool_choice", {})
        if choice.get("type") == "tool":
            payload["toolConfig"]["toolChoice"] = {"tool": {"name": choice["name"]}}
        elif choice.get("type") in ("any", "auto"):
            payload["toolConfig"]["toolChoice"] = {choice["type"]: {}}
    if isinstance(body.get("thinking"), dict):
        payload["additionalModelRequestFields"] = {"thinking": body["thinking"]}
    return payload


def bedrock_chunks(events: Iterable[dict[str, Any]]) -> Iterator[dict[str, Any]]:
    finished = False
    reasoning: dict[int, dict[str, str]] = {}
    for event in events:
        for name, error in event.items():
            if name.endswith("Exception"):
                status = 429 if name == "throttlingException" else 502
                response = httpx.Response(status, text=str(error))
                raise_for_status(response, provider="amazon-bedrock")
        if "contentBlockStart" in event:
            entry = event["contentBlockStart"]
            tool = entry.get("start", {}).get("toolUse")
            if tool:
                yield {"type": "tool_call_start", "index": entry["contentBlockIndex"], "id": tool["toolUseId"], "name": tool["name"], "arguments": ""}
        if "contentBlockDelta" in event:
            entry = event["contentBlockDelta"]
            delta = entry.get("delta", {})
            index = entry["contentBlockIndex"]
            if isinstance(delta.get("text"), str):
                yield {"type": "text_delta", "text": delta["text"]}
            if "toolUse" in delta:
                yield {"type": "tool_call_delta", "index": index, "arguments": delta["toolUse"].get("input", "")}
            thought = delta.get("reasoningContent")
            if isinstance(thought, dict):
                state = reasoning.setdefault(index, {"text": "", "signature": ""})
                if isinstance(thought.get("text"), str):
                    state["text"] += thought["text"]
                    yield {"type": "reasoning_delta", "text": thought["text"]}
                if isinstance(thought.get("signature"), str):
                    state["signature"] += thought["signature"]
        if "contentBlockStop" in event:
            state = reasoning.pop(event["contentBlockStop"]["contentBlockIndex"], None)
            if state and state["signature"]:
                yield {"type": "reasoning_details", "details": [{"type": "bedrock_reasoning", **state}]}
        if "metadata" in event:
            usage = event["metadata"].get("usage", {})
            yield {"type": "usage", "usage": {"prompt_tokens": usage.get("inputTokens", 0), "completion_tokens": usage.get("outputTokens", 0), "total_tokens": usage.get("totalTokens", 0)}}
        if "messageStop" in event:
            reason = event["messageStop"].get("stopReason")
            finished = True
            yield {"type": "finish", "reason": {"tool_use": "tool_calls", "max_tokens": "length"}.get(reason, "stop")}
    if not finished:
        raise ProviderError(502, "amazon-bedrock: event stream ended before messageStop")


class BedrockProvider(KeyedProvider):
    id = "amazon-bedrock"
    display_name = "Amazon Bedrock"

    @classmethod
    def check_account(cls, data: dict[str, Any]) -> None:
        if not (account_token(Account(data)) or (data.get("aws_access_key_id") and data.get("aws_secret_access_key")) or data.get("profile") or data.get("auth") == "aws"):
            raise ValidationError("amazon-bedrock: supply api_key, AWS credentials, profile or auth=aws")

    def fetch_models(self) -> list[str]:
        if any(isinstance(account.get("models", self.options.get("models")), list) for account in self.accounts):
            return super().fetch_models()
        # Inference profiles/ARNs are account-specific; operators may declare
        # them instead of listing globally available base models.
        try:
            import boto3
        except ImportError:
            return []
        rows: dict[str, dict[str, Any]] = {}
        for account in self.accounts:
            if account_token(account):
                continue  # Bearer keys do not authorize a control-plane listing.
            with self._session(boto3, account).client("bedrock", region_name=self._region(account)) as sdk:
                for row in sdk.list_foundation_models(byOutputModality="TEXT")["modelSummaries"]:
                    if row.get("responseStreamingSupported") is not True:
                        continue
                    model = row["modelId"]
                    rows[model] = {"id": model, "name": row.get("modelName", model), "input_modalities": [str(item).lower() for item in row.get("inputModalities", [])]}
        self.listing_rows.update(rows)
        return list(rows)

    def _region(self, account: Account) -> str:
        return str(account.get("region") or self.options.get("region") or "us-east-1")

    @staticmethod
    def _session(boto3: Any, account: Account) -> Any:
        BedrockProvider.check_account(account.data)
        fields = {key: account.get(key) for key in ("aws_access_key_id", "aws_secret_access_key", "aws_session_token") if account.get(key)}
        if account.get("profile"):
            fields["profile_name"] = account.get("profile")
        return boto3.Session(**fields)

    def stream_raw(self, account: Account, model: str, body: dict[str, Any]) -> Iterator[dict[str, Any]]:
        try:
            import boto3
            from botocore.eventstream import EventStreamBuffer
            from botocore.exceptions import ClientError
        except ImportError:
            raise ProviderError(400, "Install hoshimi[bedrock] to use Amazon Bedrock") from None
        if account_token(account):
            region = self._region(account)
            base = str(account.get("base_url") or self.options.get("base_url") or f"https://bedrock-runtime.{region}.amazonaws.com").rstrip("/")
            payload = bedrock_payload(body, model, binary_images=False)
            payload.pop("modelId")
            with self.client.stream("POST", f"{base}/model/{quote(model, safe='')}/converse-stream", json=payload,
                                    headers={"Authorization": f"Bearer {account_token(account)}", "Content-Type": "application/json"}) as response:
                observe_response(self.id, response)
                raise_for_status(response, provider=self.id)
                def events() -> Iterator[dict[str, Any]]:
                    buffer = EventStreamBuffer()
                    for data in response.iter_bytes():
                        buffer.add_data(data)
                        for message in buffer:
                            payload = json.loads(message.payload)
                            yield {message.headers.get(":event-type") or message.headers.get(":exception-type"): payload}
                yield from bedrock_chunks(events())
            return
        try:
            with self._session(boto3, account).client("bedrock-runtime", region_name=self._region(account)) as sdk:
                response = sdk.converse_stream(**bedrock_payload(body, model))
                stream = response["stream"]
                try:
                    yield from bedrock_chunks(stream)
                finally:
                    stream.close()
        except ClientError as exc:
            status = exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode", 502)
            raise_for_status(httpx.Response(status, text=str(exc.response.get("Error", {}))), provider=self.id)
