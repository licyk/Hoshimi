# Anthropic 与 Agent 接入

[文档目录](README.zh-CN.md) | [English](anthropic-api.md)

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
网关无状态，每轮发送完整历史。
不支持服务端管理的 `mcp_servers`、`container`、`context_management`、服务端工具和
结构化输出 `output_config.format`；支持下述客户端函数工具。

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
redacted-thinking 保存不透明回放数据；其他上游推理细节可封装为 Hoshimi 签名，
下一次请求时解码。不要伪造、编辑签名或跨模型、供应商复用。
签名保留协议数据，不代表每个模型都支持思考。

`thinking.type` 支持 enabled、disabled、adaptive。启用预算至少 1,024，且小于
`max_tokens`。原生 Messages 可直接携带思考与输出配置，其他协议映射到支持的强度控制。
支持的原生 Messages 路径保留 cache-control 提示，跨协议不承诺等价缓存。
菜单和预算见[模型与运行行为](model-behavior.zh-CN.md)。

## Token 估算

`POST /v1/messages/count_tokens` 校验并解析模型后，返回 `{"input_tokens": ...}`，
响应头 `x-hoshimi-token-count: estimate`。估算时 `max_tokens` 可省略。
算法以 system、messages、tools、tool_choice 的 UTF-8 大小粗略估计，不调用上游分词器，
不代表实际计费或精确上下文占用。

## 流式与错误

SSE 使用 Anthropic 消息和内容块事件。开始流式输出前的错误尽可能保留 HTTP 状态，
之后通过流错误事件报告。错误为 Anthropic 格式，响应携带请求 ID。
开始输出后不会重放请求。完整字段见运行时 `/docs`，其他协议见 [API](api.zh-CN.md)。
