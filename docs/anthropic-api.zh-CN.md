# Anthropic 与 Agent 接入

[文档目录](README.zh-CN.md) | [English](anthropic-api.md)

## 原生转发与协议转换

常规供应商账户提供原生 Messages 端点时，网关只调整路由模型名，保留原始请求。
system 数组、内容顺序、签名、工具、缓存标记、上下文管理、结构化输出和未来扩展字段
由上游校验；`anthropic-*` 请求头和查询参数一起传递。上游鉴权使用所选账户凭据，
OAuth 账户同时保留适配器要求的 OAuth Beta 能力。

选择路径时检查实际账户协议和适配器端点，不仅依据模型能力标签。兼容账户复用现有
续期、轮换和统计，原生失败不会静默切换其他协议。使用专用封装的供应商继续通过
转换器处理。端点采用 Anthropic 格式不代表支持所有扩展，上游拒绝会原样返回。

## 连接 Agent

Anthropic 兼容客户端的 Base URL 为 `http://127.0.0.1:10600`，模型从 `/v1/models`
选择。使用网关的模型 API 密钥，通过 `x-api-key` 或 Bearer 传入；工具密钥和上游凭据
用途不同，见[鉴权](authentication.zh-CN.md)。

```bash
curl http://127.0.0.1:10600/v1/messages \
  -H "x-api-key: <model-api-key>" \
  -H "anthropic-version: 2023-06-01" \
  -H "Content-Type: application/json" \
  -d '{"model":"<model-id>","max_tokens":1024,"messages":[{"role":"user","content":"你好"}]}'
```

替换占位值；推理鉴权关闭时可以省略密钥头。默认 JSON，加入 `"stream": true` 使用 SSE。
网关不保存会话历史，每轮发送完整历史；原生上游功能可以维护自己的服务端状态。

## Claude Code 配置

例如在 Shell 中：

```bash
export ANTHROPIC_BASE_URL="http://127.0.0.1:10600"
export ANTHROPIC_API_KEY="your-model-api-key"
unset ANTHROPIC_AUTH_TOKEN
claude --model "anthropic/your-claude-model-id"
```

使用模型 API 密钥。清除旧 AUTH_TOKEN 可避免 Authorization 覆盖新 API Key。
未开启网关推理鉴权时，Claude Code 可使用非空占位密钥。
通过 `ANTHROPIC_DEFAULT_HAIKU_MODEL` 指定可用的后台模型；使用 Sonnet、Opus 别名时，
相应配置 `ANTHROPIC_DEFAULT_SONNET_MODEL`、`ANTHROPIC_DEFAULT_OPUS_MODEL`。
默认模型 ID 不存在属于路由问题。

上游缺少 Beta 能力时，可用 `CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS=1` 缩小功能范围
帮助定位。具体行为与 Claude Code 版本有关，不会移除所有 Beta、effort 或 adaptive
thinking，也不能替代原生协议保留。

可选模型发现使用 `CLAUDE_CODE_ENABLE_GATEWAY_MODEL_DISCOVERY=1`。
`GET /v1/models?limit=1000` 最多等待目录发现两秒，慢供应商保留缓存、显式声明或回退
ID，后台继续发现。Claude Code 自身会筛选包含 claude 或 anthropic 的 ID；其他 ID
通过显式模型配置选择，网关不会将其他模型改名伪装成 Claude。

## 内容和工具调用

接口将文本、支持的图片块、系统提示、`tool_use` 和 `tool_result` 转为统一事件。
工具按 Anthropic 的 `name`、`description`、`input_schema` 声明，例如：

```json
{
  "model": "<model-id>",
  "max_tokens": 1024,
  "tools": [{"name": "lookup", "description": "查询条目", "input_schema": {"type": "object", "properties": {"id": {"type": "string"}}, "required": ["id"]}}],
  "tool_choice": {"type": "auto"},
  "messages": [{"role": "user", "content": "查询条目 1"}]
}
```

`tool_choice` 支持 auto、any、none，以及带已声明名称的 tool。
下一条用户消息用 `tool_result` 回传结果，`tool_use_id` 对应上次调用。
工具由客户端执行，网关负责协议转换，实际能力仍由所选模型决定。

## 思考与签名回放

回传助手历史时，保留原始思考块、签名和工具调用。原生 Anthropic 的 thinking 与
redacted-thinking 通过原生路径完整保留；其他上游推理细节可封装为 Hoshimi 签名，
下一次请求时解码。不要伪造、编辑签名或跨模型、供应商复用。
签名保留协议数据，不代表每个模型都支持思考。

转换路径的 `thinking.type` 支持 enabled、disabled、adaptive。启用预算至少 1,024，且小于
`max_tokens`。原生 Messages 直接携带思考与输出配置，其他协议映射到支持的强度控制，
包括 xhigh 档位。
支持的原生 Messages 路径保留 cache-control 提示，跨协议不承诺等价缓存。
菜单和预算见[模型与运行行为](model-behavior.zh-CN.md)。

## Token 估算

`POST /v1/messages/count_tokens` 优先调用已声明的原生计数端点，保留请求体和协议头。
Anthropic 账户默认启用，其他常规供应商可通过
`providers.<id>.options.messages_count_tokens: true` 开启。

没有声明计数端点，或端点返回 404、405、501 时，回退到 `{"input_tokens": ...}`，
响应头为 `x-hoshimi-token-count: estimate`。回退可以接收原生文档和工具块，按 UTF-8
大小估算，不调用分词器。鉴权、额度和服务端错误不会被隐藏成估算。
`max_tokens` 可省略；计数不生成回答，也不新增推理用量记录。

## 转换路径的能力边界

| 功能 | 转换行为 |
| --- | --- |
| 客户端函数工具 | 提前提供完整定义；延迟工具引用按本次请求的工具表展开 |
| 文本文档 | 文本源转换为文本；二进制或 URL 文档需要原生上游 |
| 思考与强度 | 映射到目标模型支持的控制 |
| 结构化输出 | 需 `options.structured_outputs: true`，JSON Schema 映射到 Chat 的 response_format 或 Responses 的 text.format |
| 上下文管理、容器、MCP connector、服务端工具、任务预算 | 需要原生支持，无法转换时明确指出字段与供应商 |
| 缓存标记与工具示例 | 原生保留；转换不保证等价缓存或示例语义 |

仅在所选上游和模型确实支持时开启 structured_outputs。
常规 Messages 供应商需要自定义兼容转换时，可用 `options.messages_passthrough: false`
显式关闭原生转发。

## 流式与错误

原生 SSE 保留上游事件帧，包括未知事件、用量、签名和 ping；等待时网关只在完整事件
之间发送心跳，不插入 JSON 片段内部。原生 JSON 响应也原样保留。
截断会报告错误，不伪造成功结束；客户端断开时关闭原生响应，开始输出后不轮换重放。

原生错误保留状态、正文、请求 ID 和协议头，包括 x-should-retry、整数秒 retry-after、
anthropic-ratelimit-*。显式 `x-should-retry: false` 会停止网关重试。
网关自行生成的错误使用 Anthropic 格式，转换输出使用统一消息和内容块事件。
完整字段见运行时 `/docs`，其他协议见 [API](api.zh-CN.md)。

## 协议依据与验证

- [Messages HTTP 参考](https://platform.claude.com/docs/en/api/http/messages/create)
- [Claude Code 网关兼容指南](https://code.claude.com/docs/en/llm-gateway-protocol)
- [流式事件](https://platform.claude.com/docs/en/build-with-claude/streaming)
- [工具搜索与引用](https://platform.claude.com/docs/en/agents-and-tools/tool-use/tool-search-tool)
- [思考强度](https://platform.claude.com/docs/en/build-with-claude/effort)

开发依赖包含官方 Anthropic SDK，协议测试通过本地模拟上游，由真实 SDK 消费 JSON 和
流式结果，并覆盖账户轮换、签名回放、计数、心跳、取消和截断。
真实账户可用性与模型功能需分别验证。
