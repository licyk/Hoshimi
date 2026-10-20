# Providers

[Documentation](README.md) | [简体中文](providers.zh-CN.md)

## Entry points and accounts

Use a native provider for its specific endpoint, protocol and plan, or `agg` for
a generic compatible service. Cloud APIs, subscription/free stations and local
backends share the account management UI. The [generated inventory](provider-inventory.md)
maps pi, pi-free and free-claude-code source entries and deduplication coverage.

The runtime registry is `hoshimi/core/providers/factory.py`. Inventory rows do
not establish live availability. Credential fields are described by
`hoshimi/core/provider_forms.py` and `/api/v1/provider-forms`.

## Native account reference

This table covers the original station lanes. New cloud APIs are also available;
see [cloud platforms](cloud-providers.md), [NVIDIA](nvidia.md) and the inventory.
Builtin IDs below describe historical fallback data when discovery is unavailable;
current model access depends on the upstream and account. `kilo` and `opencode`
start anonymously without configuration; set `enabled: false` to disable one.

| id | upstream | account fields | refresh | builtin models |
| --- | --- | --- | --- | --- |
| `cline` | `api.cline.bot` | `access_token`, `refresh_token` | yes (workos) | `stealth/space-bunny-alpha`, `cline-free/*` |
| `codex` | ChatGPT/Codex OAuth + Responses | `access_token`, `refresh_token`, `account_id`, `client_id`, `expires_at` | yes | account catalog or configured models |
| `lobsterai` | `lobsterai-server.youdao.com` | `access_token`, `refresh_token`, `uuid` | yes | `deepseek-v4-flash`, `MiniMax-M3`, ... |
| `loomy` | `loomyad.xunfei.cn` | `access_token` (also sent as `token` header) | no | `deepseek-v4-flash-0731`, `spark-x`, ... |
| `buddy` (CodeBuddy) | `copilot.tencent.com` | `access_token`, `refresh_token` | yes | 16 (hy4/hy3, deepseek-v4, glm-5.x, kimi-k2/3, minimax-m3) |
| `workbuddy` | `www.workbuddy.ai` | `access_token`, `refresh_token` | yes | 23 (gpt-5.x, gemini-3.5-flash, glm/kimi, ...) |
| `qoder` | `openapi.qoder.sh` | `access_token`, `security_oauth_token`, `refresh_token`, `machine_id` | yes | `auto`, `qmodel_38max`, `gmodel`, ... |
| `qodercn` | `openapi.qoder.com.cn` | same as `qoder` | yes | same as `qoder` |
| `trae` | `trae-api-cn.mchost.guru` | `access_token`, `refresh_token`, `device_id`, `machine_id`, `uid` | yes | `glm-5.2`, `DeepSeek-V4-*`, `kimi-k*`, ... |
| `raccoon` | `xiaohuanxiong.com` | `access_token`, `refresh_token`, `device_id`, `office_identity` | yes | `sn-glm-5-3`, `sn-sensenova-6-8-*`, ... |
| `minimax` | `agent.minimax.cn` | `access_token`, `refresh_token` | yes | `MiniMax-M3.1-Flash-Preview`, `MiniMax-M3`, ... |
| `zcode` | `zcode.z.ai` | `zcode_jwt` (or `access_token`), `device_mid` | no | `GLM-5.3-Flash`, `GLM-5.3` |
| `opencode` | `opencode.ai` | `api_key`, `project_id` (anonymous lane: none) | no | `big-pickle`, `mimo-v2.6-flash-free`, ... |
| `kilo` | `api.kilo.ai` | optional API key or device login; keyless free pool by default | no | 16 free ids (`openrouter/free`, `kilo-auto/free`, ...) |
| `gemini` | `daily-cloudcode-pa.googleapis.com` | `access_token`, `refresh_token` (+ `client_id`/`client_secret` in options) | yes | `gemini-3.8-flash` (effort menu: low / medium / high / tiered) |
| `codearts` | ModelArts `snap-access.cn-north-4` | `access_key_id`, `secret_access_key`, `security_token` | no | `GLM-5.x`, `deepseek-v4*`, `openpangu-2.0-*`, ... |
| `eac` | bundled or custom co-paid relay | fresh login uses `relay: "bundled"`; custom `base_url` + `access_token` (direct) or `signing_secret` (worker); `user_token` = this install's GitHub authorization (auto-loaded from `eac-user.json`) | no | `deepseek-ai/deepseek-v4.1-flash`, `moonshotai/kimi-*`, `z-ai/glm-5.3*`, `openai/gpt-oss-20b`, plus the two relayed free-lane ids (`opencode/deepseek-v4.1-flash-free`, `opencode/glm-5.3-flash-free`) |
| `agg` | any OpenAI-compatible station (`base_url`) | `base_url` + `api_key` (or `access_token`/`token`); optional `models`, `headers`, `options` | no | the station's own `/models`, or the account's `models` list |
| `zhipu` | `open.bigmodel.cn/api/paas/v4` | `api_key` (from the bigmodel.cn console) | no | 7 historical free fallback ids (`glm-4.7-flash`, `glm-z1-flash`, `glm-4.6v-flash`, ...) |

## Aggregator (`agg`)

Each account represents a station with its own OpenAI root and credential:

```json
{
  "providers": {
    "agg": {
      "accounts": [{
        "id": "my-service", "base_url": "https://example.com/v1",
        "api_key": "your-provider-api-key", "models": ["your-model-id"]
      }]
    }
  }
}
```

- `base_url` is the API root for `/chat/completions` and `/models`. `preset`
  can name one of the 22 known stations instead; an explicit URL wins.
  `hoshimi stations` lists presets. Its environment-variable names describe the
  upstream convention and are not automatically read as Hoshimi credentials.
- Credentials accept `api_key`, `access_token` or `token`. Missing URLs, unknown
  presets or missing keys cause a startup warning and the account is dropped;
  account additions through the UI are validated before saving.
- `models` declares the account's catalogue and restricts those IDs to accounts
  advertising them. Without it, the upstream listing is used.
- `options` on an account overrides provider options. Declare `vision`,
  `reasoning`, `context_window` and `max_output`, with per-ID overrides under
  `model_specs`. Menus use `efforts`, `effort_patch` and `effort_off_patch`.
  CommandCode's station table supplies capabilities before account overrides;
  missing capability evidence never invents a thinking menu.
- `headers` adds upstream headers but cannot replace Authorization.

Task filters match endpoint addresses, retain unknown metadata and keep LLM7's
own rules. Free status remains free/paid/unknown. See [pi-free compatibility](pi-free.md)
for the station-specific rules and differences, and [model behavior](model-behavior.md)
for capability precedence. Keep real credentials out of shared configurations.

## Zhipu (`zhipu`)

```json
{
  "providers": {
    "zhipu": {"accounts": [{"id": "zhipu-1", "api_key": "your-bigmodel-api-key"}]}
  }
}
```

The default root is `https://open.bigmodel.cn/api/paas/v4`;
`options.base_url` can select `https://api.z.ai/api/paas/v4` instead.
Live discovery is preferred. The fallback free slice contains `glm-4.7-flash`,
`glm-4.5-flash`, `glm-4-flash-250414`, `glm-z1-flash`, `glm-4.6v-flash`,
`glm-4.1v-thinking-flash` and `glm-4v-flash`. Current access still depends on
the upstream and quota. Text models without menus preserve caller fields;
reasoning-by-construction Z1/V-Thinking models use the budget ladder.

## Local backends

`lmstudio`, `ollama` and `llamacpp` connect only after an account is explicitly
added and the provider enabled; Hoshimi does not scan local ports. Omit `api_key`
if the local server accepts anonymous requests. For example:

```json
{
  "providers": {
    "ollama": {"accounts": [{"id": "local", "base_url": "http://127.0.0.1:11434/v1"}]}
  }
}
```

LM Studio uses loaded-model context limits and a rough preflight. Ollama reads
its native capability catalogue. llama.cpp maps thinking controls to template
parameters. Local models are not automatically marked free.

## Provider and plan distinctions

`minimax` is Agent login; `minimax-api`, `minimax-cn` and `minimax-chat` are
separate API services. `gemini` is Code Assist, `google` is Gemini Developer API,
and `google-vertex` is Vertex. `amazon-bedrock` and `bedrock-mantle` are configured
separately. TypeSafe exposes classification, not chat.

## Provider options and integration limits

- TRAE: `channel` (default `solo_work_lite`), `user_agent`, `app_id`, `ide_version`,
  `ide_version_code`, `os_version`, `device_brand`.
- ZCode: `app_version` (default `3.14.3`).
- CodeArts: `benefit_models` (default `glm-5.3-flash`, `deepseek-v4.1-flash`);
  rejected benefit headers are retried without that header.
- Gemini Code Assist: `client_id`, `client_secret` for OAuth refresh.
- Zhipu: `base_url` to select the station root.

Qoder's reference implementation uses an encrypted WASM inference path; its plain
public Chat endpoint may return `503`. Matching headers, models and refresh
behavior does not establish live inference parity.

See [authentication](authentication.md) for login, renewal and EAC;
[cloud platforms](cloud-providers.md), [NVIDIA](nvidia.md) and
[OpenCode](opencode-integration.md) for specialized integrations.
