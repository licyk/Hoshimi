# 云平台与特殊协议

[文档目录](README.zh-CN.md) | [English](cloud-providers.md)

## 选择入口

按端点、协议、凭据和套餐选择供应商：`gemini` 是 Code Assist，`google` 是 Gemini
Developer API，`google-vertex` 是 Vertex；`amazon-bedrock` 与 `bedrock-mantle`
分别配置。模型同名不代表凭据可以互换。

以下示例写入数据目录配置，并替换占位值，仅启用所需供应商。文件修改后重启，渠道添加
账户可实时生效。位置和密钥用途见[配置](configuration.zh-CN.md)与
[鉴权](authentication.zh-CN.md)。

## 可选 SDK

```bash
python -m pip install -U "hoshimi[google]"   # Vertex ADC / 服务账户
python -m pip install -U "hoshimi[bedrock]"  # SDK 凭据与 Bearer 事件流
```

同时使用可安装 `"hoshimi[google,bedrock]"`。Vertex API Key 和普通 HTTP 供应商无需
这些 SDK，但 Bedrock Bearer 模式仍需扩展中的事件流解析器。缺少依赖或凭据不表示
可以匿名使用。

## Azure OpenAI

配置资源名称、密钥、模型及部署映射：

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

默认根地址为 `https://{resource}.openai.azure.com/openai/v1`，可用 `base_url` 替代。
鉴权使用 `api-key`。部署映射只改变上游模型名，能力和协议仍按公开 ID 查找。
默认协议为 Responses，账户 `protocol: "chat"` 可选择 Chat Completions。

## Cloudflare

Workers AI 需要账户 ID，AI Gateway 还需要网关 ID：

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

Workers 使用原生搜索目录并过滤非文本生成任务；`models` 可替代发现。
Gateway Chat 使用 `/compat` 和 `cf-aig-authorization`，Messages、Responses
选择各自协议端点。账户 `protocol` 或供应商模型协议选项应匹配上游实际支持的协议。

## Gemini Developer API

```json
{
  "providers": {
    "google": {"accounts": [{"id": "google-1", "api_key": "your-key", "protocol": "gemini"}]}
  }
}
```

默认使用原生 Gemini 流式协议和 `x-goog-api-key`，`protocol: "chat"` 切换 OpenAI
兼容端点。发现使用原生分页目录，原生转换保留函数调用和思考签名供回放。
这里不使用 `gemini` 下的 Code Assist OAuth 账户。

## Google Vertex AI

可选 API Key、应用默认凭据 ADC 或服务账户文件：

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

服务账户将 `auth: "adc"` 替换为 `service_account_file: "/path/to/service-account.json"`；
API Key 模式改为提供 `api_key`。ADC 和服务账户需要 Google 扩展及服务进程可访问的
凭据。ADC 和服务账户路由要求 project，location 决定区域或 global 路由。
凭据缺失会报错，不静默切换模式。显式连接兼容端点时可用 `protocol: "chat"`。
声明的模型 ID 可代替发布者目录。

## Amazon Bedrock

SDK 模式支持显式 AWS 凭据、命名 profile 和默认凭据链。例如：

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

命名配置使用 `profile`，显式凭据使用 `aws_access_key_id`、`aws_secret_access_key` 和
可选 `aws_session_token`。这些模式需要 Bedrock 扩展，使用 ConverseStream。
账户 `api_key` 则使用 Bearer HTTP 事件流，同样需要 Bedrock 扩展，并需声明 `models`，因为 Bearer 不授予
控制面目录权限。SDK 可以发现文本基础模型，但账户特有的推理 profile ID 或 ARN
应显式声明。

适配器转换消息、支持的图片、工具调用与结果、用量和思考签名，实际能力依模型而异。
默认区域为 `us-east-1`，仍需具备模型访问权限。
`bedrock-mantle` 使用自己的端点和密钥单独配置。

## Radius 与 TypeSafe

Radius 使用专用 pi-messages 适配器，不是通用 Chat 端点；支持密钥或对应登录流程，
通过 `/v1/config` 发现目录。TypeSafe 使用 `typesafe`、API Key 与可选 `classifiers`
列表，通过 `GET /v1/classifiers` 和 `POST /v1/classify` 提供分类，不属于聊天模型。
分类字段见运行时 `/docs`。

完整套餐与服务映射见[供应商](providers.zh-CN.md)和[生成清单](provider-inventory.md)。
本页描述已实现的配置行为，实时可用性仍需通过实际账户请求确认。
