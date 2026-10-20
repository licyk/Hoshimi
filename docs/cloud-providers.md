# Cloud platforms and special protocols

[Documentation](README.md) | [简体中文](cloud-providers.zh-CN.md)

## Choosing an integration

Use the provider matching the upstream endpoint, protocol, credentials and plan.
`gemini` is Code Assist; `google` is Gemini Developer API; `google-vertex` is
Vertex. `amazon-bedrock` and `bedrock-mantle` are separate services. A similar
model name does not make their credentials interchangeable.

Examples below are `providers` entries in your data-directory configuration,
not files to save beside the executable. Replace placeholders, enable only the
providers you use, and restart after file edits. Channels can add accounts live.
See [configuration](configuration.md) and [authentication](authentication.md).

## Optional SDKs

```bash
python -m pip install -U "hoshimi[google]"   # Vertex ADC / service accounts
python -m pip install -U "hoshimi[bedrock]"  # SDK credentials and bearer event streams
```

`"hoshimi[google,bedrock]"` installs both. Vertex API-key mode and ordinary HTTP
providers do not need these SDKs. Bedrock bearer mode still needs the Bedrock
extra's event-stream decoder. Missing credentials/dependencies are not anonymous access.

## Azure OpenAI

Configure the Azure resource name, key and model/deployment list:

```json
{
  "providers": {
    "azure": {
      "accounts": [{
        "id": "azure-1", "resource": "your-resource", "api_key": "your-key",
        "models": ["public-model-id"],
        "deployments": {"public-model-id": "your-deployment-name"}
      }]
    }
  }
}
```

The default root is `https://{resource}.openai.azure.com/openai/v1`; a `base_url`
can replace it. Requests use `api-key`. Deployment mapping changes the upstream
request's model while capability/protocol lookup keeps the public ID. Responses
is the default wire; account `protocol: "chat"` can select Chat Completions.

## Cloudflare

Workers AI uses an account ID; AI Gateway also needs a gateway ID:

```json
{
  "providers": {
    "cloudflare-workers-ai": {
      "accounts": [{"id": "workers-1", "account_id": "your-account-id", "api_key": "your-token"}]
    },
    "cloudflare-ai-gateway": {
      "accounts": [{"id": "gateway-1", "account_id": "your-account-id", "gateway_id": "your-gateway-id", "api_key": "your-token"}]
    }
  }
}
```

Workers discovery reads its native model search and filters non-text-generation
tasks. A configured `models` list can replace discovery. Gateway Chat uses the
`/compat` endpoint and `cf-aig-authorization`; Messages and Responses select
their corresponding protocol endpoints. Use `protocol` or provider model protocol
options only for a wire the configured upstream actually supports.

## Gemini Developer API

```json
{
  "providers": {
    "google": {"accounts": [{"id": "google-1", "api_key": "your-key", "protocol": "gemini"}]}
  }
}
```

The default is native Gemini streaming with `x-goog-api-key`.
`protocol: "chat"` selects the OpenAI-compatible Chat endpoint. Discovery uses
the native model catalogue with pagination. The native translator preserves
function calls and their opaque thought signatures for replay. This API does not
use the Code Assist OAuth account configured under `gemini`.

## Google Vertex AI

Choose API key, application default credentials, or a service account file:

```json
{
  "providers": {
    "google-vertex": {
      "accounts": [{
        "id": "vertex-1", "auth": "adc", "project": "your-project",
        "location": "us-central1", "protocol": "gemini",
        "models": ["your-model-id"]
      }]
    }
  }
}
```

For service accounts, replace `auth: "adc"` with
`service_account_file: "/path/to/service-account.json"`. For API-key mode, supply
`api_key` instead. ADC/service accounts need the Google extra and credentials
available to the server process. ADC/service-account routing requires a project;
location chooses regional/global routing. A missing credential is reported rather
than silently falling back to another mode. Use `protocol: "chat"` when explicitly
connecting to Vertex's supported OpenAI-compatible endpoint. Configured model IDs
can stand in for the publisher catalogue.

## Amazon Bedrock

SDK modes support explicit AWS credentials, a named profile or the AWS credential
chain. For example:

```json
{
  "providers": {
    "amazon-bedrock": {
      "accounts": [{
        "id": "bedrock-1", "auth": "aws", "region": "us-east-1",
        "models": ["your-model-or-inference-profile-id"]
      }]
    }
  }
}
```

Use `profile` for a named profile, or `aws_access_key_id`, `aws_secret_access_key`
and optional `aws_session_token` for explicit credentials. These modes use the
Bedrock extra and ConverseStream. Alternatively, an account `api_key` uses the
bearer HTTP event-stream mode, also requiring the Bedrock extra; declare `models`
because bearer keys do not grant
the control-plane model listing. SDK discovery can list text foundation models;
account-specific inference profile IDs/ARNs should be declared explicitly.

The adapter maps messages, supported images, tool use/results, usage and reasoning
signatures to the gateway events. Not every Bedrock model supports each feature.
Region defaults to `us-east-1`; authorization and model access remain account
requirements. `bedrock-mantle` is configured separately with its own endpoint/key.

## Radius and TypeSafe

Radius uses a dedicated pi-messages adapter rather than a generic Chat endpoint.
Add its key or use its supported login flow; directory discovery reads `/v1/config`.
TypeSafe uses the `typesafe` provider with an API key and optional `classifiers`
list, exposed through `GET /v1/classifiers` and `POST /v1/classify`. It is not a
chat model provider. Runtime `/docs` describes the classification request fields.

For the full service/plan mapping, see [providers](providers.md) and the generated
[inventory](provider-inventory.md). Live availability must be established with the
configured account; this guide describes implemented configuration behavior.
