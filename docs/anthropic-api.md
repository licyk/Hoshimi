# Anthropic and agent integration

[Documentation](README.md) | [简体中文](anthropic-api.zh-CN.md)

## Native and translated requests

For conventional providers exposing a native Messages endpoint, Hoshimi forwards
the original request with only the routed model name changed. System arrays,
content order, opaque signatures, tools, cache markers, context management,
structured output and future fields are left for that upstream to validate.
Incoming `anthropic-*` headers and query parameters are retained; upstream
credentials come from the selected Hoshimi account. OAuth accounts also retain
the adapter's required OAuth beta capabilities.

Selection checks each eligible account's actual protocol and adapter endpoint,
not just a model's capability label. Compatible accounts share existing renewal,
rotation and accounting; native failures do not silently switch to a different
protocol. Dedicated providers with custom envelopes continue through their
translators. An Anthropic-shaped endpoint can still reject features it does not
implement; the original error is returned to the client.

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
Send the full conversation history with each turn. The gateway does not store
conversation history; native upstream features may maintain their own state.

## Claude Code configuration

For example, in a shell:

```bash
export ANTHROPIC_BASE_URL="http://127.0.0.1:10600"
export ANTHROPIC_API_KEY="your-model-api-key"
unset ANTHROPIC_AUTH_TOKEN
claude --model "anthropic/your-claude-model-id"
```

Use the gateway's model key, not the tool key. Clearing a stale auth token avoids
an Authorization header overriding the API key. If gateway inference authentication
is disabled, Claude Code can use a non-empty placeholder key. To pin background
requests too, set `ANTHROPIC_DEFAULT_HAIKU_MODEL` to an available gateway ID;
configure `ANTHROPIC_DEFAULT_SONNET_MODEL` and `ANTHROPIC_DEFAULT_OPUS_MODEL` when
using those aliases. Missing default-model IDs are routing errors.

For upstreams without beta capabilities, `CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS=1`
can narrow the feature set for diagnosis. Its behavior depends on Claude Code's
version; it does not remove every beta header, effort or adaptive thinking.
It is not a substitute for native protocol preservation.

Optional gateway discovery uses `CLAUDE_CODE_ENABLE_GATEWAY_MODEL_DISCOVERY=1`.
`GET /v1/models?limit=1000` bounds discovery waiting to two seconds, keeps cached,
configured or fallback IDs for slow providers and lets discovery finish in the
background. Claude Code itself filters picker IDs for `claude` or `anthropic`;
use explicit model configuration for IDs outside that filter. The gateway does
not rename other models to pretend they are Claude.

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
Native forwarding preserves thinking/redacted-thinking blocks without reconstruction;
other upstream reasoning details can be encoded in a Hoshimi signature envelope
and decoded on the next request. Do not synthesize, edit or reuse signatures for
another model or provider. A signature preserves protocol data; it is not a
promise that every upstream supports thinking.

For translated requests, `thinking.type` accepts `enabled`, `disabled` and `adaptive`. An enabled budget
must be at least 1,024 and smaller than `max_tokens`. Native Messages can carry
thinking/output configuration directly; other wires map it to supported effort
controls, including mapping `xhigh` effort. Cache-control hints are preserved for native Messages paths;
cross-protocol translation does not promise equivalent caching. See
[model behavior](model-behavior.md) for menus and budgets.

## Token counting

`POST /v1/messages/count_tokens` delegates to a declared native counting endpoint
when available, preserving the original body and protocol headers. Anthropic
accounts enable native counting by default. Other conventional providers can opt
in with `providers.<id>.options.messages_count_tokens: true`.

If no counting endpoint is declared, or it returns `404`, `405` or `501`, the
gateway returns `{"input_tokens": ...}` with `x-hoshimi-token-count: estimate`.
This fallback accepts native document/tool blocks and approximates UTF-8 size;
it does not invoke a tokenizer. Authentication, quota and server errors are not
hidden as estimates. `max_tokens` is optional. Counting does not generate a reply
or add an inference usage record.

## Translated-upstream capabilities

| Feature | Translation behavior |
| --- | --- |
| Client function tools | Definitions are available upfront; deferred client-tool references expand from the request's own tool table |
| Text documents | Text sources become text content; binary/URL documents require a native upstream |
| Thinking and effort | Mapped to the selected model's supported controls |
| Structured output | Requires `options.structured_outputs: true`; JSON Schema maps to Chat `response_format` or Responses `text.format` |
| Context management, containers, MCP connector, server tools, task budgets | Require native support; return a field/provider-specific error when conversion is unavailable |
| Cache markers and tool examples | Native forwarding preserves them; conversion does not guarantee equivalent caching or example semantics |

Only enable `structured_outputs` when the chosen upstream and models support that
format. `options.messages_passthrough: false` explicitly selects compatibility
conversion for a conventional Messages provider with custom requirements.

## Streaming and errors

Native SSE preserves upstream event frames, including unknown events, usage,
signatures and pings. The gateway emits heartbeats between complete frames during
idle reads, never inside partial JSON. Native JSON responses are also retained.
Truncated streams report an error rather than a successful stop. Disconnects close
the native response; a stream that has begun is not replayed on another account.

Native errors keep their status, body, request ID and protocol headers, including
`x-should-retry`, `retry-after` (integer seconds) and `anthropic-ratelimit-*`.
An explicit `x-should-retry: false` stops gateway retries. Gateway-generated errors
use Anthropic envelopes. Translated output uses normalized message/content-block
events. Consult runtime `/docs` for schemas and [API integration](api.md) for routing.

## Protocol references and validation

- [Messages HTTP reference](https://platform.claude.com/docs/en/api/http/messages/create)
- [Claude Code gateway compatibility](https://code.claude.com/docs/en/llm-gateway-protocol)
- [Streaming events](https://platform.claude.com/docs/en/build-with-claude/streaming)
- [Tool search and references](https://platform.claude.com/docs/en/agents-and-tools/tool-use/tool-search-tool)
- [Effort](https://platform.claude.com/docs/en/build-with-claude/effort)

The development extra includes the official Anthropic SDK. Protocol tests consume
real SDK JSON and streaming helpers over local mock upstreams, alongside account
rotation, signature replay, counting, heartbeat, cancellation and truncation cases.
Live account availability and model feature support require separate verification.
