# Anthropic and agent integration

[Documentation](README.md) | [简体中文](anthropic-api.zh-CN.md)

## Connect an agent

Set the Anthropic-compatible Base URL to `http://127.0.0.1:10600` and choose a
model ID from `/v1/models`. Send the gateway's model API key as `x-api-key` or
`Authorization: Bearer`; the tool key and upstream provider credentials are
separate. See [authentication](authentication.md).

```bash
curl http://127.0.0.1:10600/v1/messages \
  -H "x-api-key: <model-api-key>" \
  -H "anthropic-version: 2023-06-01" \
  -H "Content-Type: application/json" \
  -d '{"model":"<model-id>","max_tokens":1024,"messages":[{"role":"user","content":"hi"}]}'
```

Replace both placeholders; omit the key header if inference authentication is
disabled. Messages returns JSON by default. Add `"stream": true` for SSE.
Send the full conversation history with each turn; the gateway is stateless.
Server-managed `mcp_servers`, `container` and `context_management`, server tools,
and structured output `output_config.format` are unsupported. Client function
tools remain available as described below.

## Content and tool calls

Messages translates text, supported image blocks, system prompts, `tool_use`
and `tool_result` through the gateway's normalized events. Declare tools with
Anthropic `name`, `description` and `input_schema`, for example:

```json
{
  "model": "<model-id>",
  "max_tokens": 1024,
  "tools": [{"name": "lookup", "description": "Look up an item", "input_schema": {"type": "object", "properties": {"id": {"type": "string"}}, "required": ["id"]}}],
  "tool_choice": {"type": "auto"},
  "messages": [{"role": "user", "content": "Look up item 1"}]
}
```

`tool_choice` accepts `auto`, `any`, `none`, or `tool` with a declared name. Return
tool results in the next user message, matching `tool_use_id` to the prior call.
The client executes tools; the gateway translates declarations and results.
Availability depends on the selected upstream model and its tool support.

## Thinking and signature replay

Replay assistant thinking blocks with their original signatures and tool calls.
Native Anthropic thinking/redacted-thinking blocks preserve opaque replay data;
other upstream reasoning details can be encoded in a Hoshimi signature envelope
and decoded on the next request. Do not synthesize, edit or reuse signatures for
another model or provider. A signature preserves protocol data; it is not a
promise that every upstream supports thinking.

`thinking.type` accepts `enabled`, `disabled` and `adaptive`. An enabled budget
must be at least 1,024 and smaller than `max_tokens`. Native Messages can carry
thinking/output configuration directly; other wires map it to supported effort
controls. Cache-control hints are preserved for supported native Messages paths;
cross-protocol translation does not promise equivalent caching. See
[model behavior](model-behavior.md) for menus and budgets.

## Token counting

`POST /v1/messages/count_tokens` validates and resolves the model, then returns
`{"input_tokens": ...}` with header `x-hoshimi-token-count: estimate`.
`max_tokens` is optional for counting. This is a local UTF-8-size approximation
over system, messages, tools and tool choice, not an upstream tokenizer call,
billing measurement or exact context-fit check.

## Streaming and errors

SSE uses Anthropic message/content-block events. Pre-stream failures retain their
HTTP status where possible; later failures are stream errors. Errors use Anthropic
envelopes and responses include a request ID. Once output has begun the gateway
does not replay the request. Consult runtime `/docs` for the full schema and
[API integration](api.md) for other protocols and routing.
