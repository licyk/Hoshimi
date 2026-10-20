# NVIDIA NIM

[Documentation](README.md) | [简体中文](nvidia.zh-CN.md)

## Account

Add an NVIDIA account in Channels, or put this entry in your data-directory
configuration (locate it with `hoshimi config path`):

```json
{
  "providers": {
    "nvidia": {
      "accounts": [{"id": "nvidia-1", "api_key": "your-nvidia-api-key"}]
    }
  }
}
```

Restart after file edits. UI account additions apply immediately. Query
`/v1/models` for IDs, then use `nvidia/<model-id>` to explicitly select this
provider. The default endpoint is `https://integrate.api.nvidia.com/v1`.
An upstream key is distinct from the gateway's inference and tool keys.

## Catalogue and compatibility

The adapter uses live model discovery and excludes recognized embeddings,
rerankers, reward scorers, detectors and document parsers. An ID being listed
does not prove present quota or successful inference; explicit availability
probes are supported. Capability metadata and account options follow the shared
[model rules](model-behavior.md).

Chat requests remove unsupported `store`, `reasoning_effort`, `reasoning` and
`prompt_cache_retention`. `max_completion_tokens` becomes `max_tokens` when no
`max_tokens` was provided; `developer` messages become `system`. Tool schemas
have unsupported strict/schema fields cleaned. MiniMax tool streams use the
adapter's native-tool normalization. These are provider translations, not full
OpenAI feature parity.

The adapter sends `NVCF-POLL-SECONDS: 3600`. The actual upstream key permissions,
quota and model access determine whether a call succeeds. See [API](api.md) for
client setup and [providers](providers.md) for common account configuration.
