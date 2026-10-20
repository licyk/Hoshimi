# OpenCode integration

[Documentation](README.md) | [简体中文](opencode-integration.zh-CN.md)

## Anonymous and keyed access

`opencode` starts with a synthetic anonymous account unless explicitly disabled.
Its free catalogue is the upstream `-free` slice. Add an API-key account in
Channels to access its authenticated catalogue; key permissions still apply.
Use [API routing](api.md#model-routing) to select `opencode/<model-id>`.

## Protocol behavior

The implementation follows the v2 wire in the external dsh-our-free-model
`src/upstream.js` snapshot `f0b979f`. This identifies the migration baseline,
not a claim that the remote service has just been verified.

- The native adapter uses `/inference/v1`, `user-agent: opencode/latest/2.0.26/cli`
  and `x-opencode-client: cli`; the legacy `x-opencode-request` is omitted.
- `x-opencode-session`, `x-opencode-session-id`, `x-session-affinity` and
  `x-session-id` agree. Descending-encoded session IDs are stable per account and
  conversation, derived from `session_id`, `conversation_id`, `user` or the first
  user message. Send `session_id` to distinguish conversations with the same opener.
- Anonymous calls declare the reference v2 `read`/`shell` tool schemas. Conflicting
  caller tools are aliased consistently across declarations, history and forced
  choices, then restored in responses. Injected tools are placeholders; attempting
  a tool the caller did not authorize returns an explicit error.
- `tool_choice: none` is omitted on the upstream wire, with the response guard
  still enforcing it. API-key accounts retain their original tools.
- Per-model Chat, Responses and Messages upstream routing remains in use under
  `/inference/v1`.

Live discovery is authoritative. Refresh failures retain the previous catalogue;
only a cold start needs the historical fallback list. New free IDs keep their
upstream spelling. Exact overrides for DeepSeek V4.1 Flash, GLM 5.3 Flash,
Nemotron Super VL and Space Bunny provide the reference thinking menus.

## Verification scope

`tests/core/test_opencode_lane.py`, `tests/core/test_pi_free_parity.py` and the
protocol tests describe local regression coverage. They are not substitutes for
successful requests from a particular egress. Model availability, region and
quota require current upstream responses or an explicit
[probe](model-behavior.md#availability-probes).
