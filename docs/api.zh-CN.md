# API 与客户端接入

[文档目录](README.zh-CN.md) | [English](api.md)

## 客户端连接

默认服务地址为 `http://127.0.0.1:10600`。OpenAI 兼容客户端的 Base URL 通常为
`http://127.0.0.1:10600/v1`，Anthropic 兼容客户端为 `http://127.0.0.1:10600`。
从 `/v1/models` 选择模型；已启用推理鉴权时，使用网关的模型 API 密钥。
供应商自身凭据保存在 Hoshimi 中。

```bash
curl http://127.0.0.1:10600/v1/models
curl http://127.0.0.1:10600/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"<model-id>","messages":[{"role":"user","content":"你好"}]}'
```

替换 `<model-id>`。配置了模型密钥时，为两个请求均添加
`-H "Authorization: Bearer <model-api-key>"`，也支持 `x-api-key`。
密钥用途见[鉴权](authentication.zh-CN.md)。

## 推理接口

| 接口 | 行为 |
| --- | --- |
| `POST /v1/chat/completions` | 默认流式输出，`stream: false` 返回 JSON |
| `POST /v1/responses` | 无状态 Responses，内部转换为 Chat |
| `POST /v1/messages` | 支持时原生转发，否则协议转换；默认 JSON，`stream: true` 使用 SSE |
| `POST /v1/messages/count_tokens` | 已声明时调用原生计数，否则标识为本地估算 |
| `GET /v1/models` | JSON 模型目录 |
| `GET /v1/classifiers`、`POST /v1/classify` | TypeSafe 分类 |
| `GET /health` | 公开健康检查 |

Responses 不支持 `previous_response_id` 和结构化输出 `text.format`，传入时返回
`400`，请发送完整历史。工具、支持的输入和流式事件通过协议转换器处理。
接口兼容不代表每个上游模型都支持所有功能。
Messages 详见 [Anthropic Agent 接入](anthropic-api.zh-CN.md)。
原生 Messages 保留上游扩展字段和协议、错误元信息。转换结构化输出需要供应商显式开启
`structured_outputs`，不改变上述直接 `/v1/responses` 的限制。
`GET /v1/models?limit=1000` 为 Claude Code 提供有等待上限的启动发现和展示名，
同时保留 OpenAI 兼容的 data 列表。

## 模型路由

- `供应商ID/模型ID` 显式选择已配置供应商，第一个斜杠之后的完整内容作为上游模型 ID。
- 裸模型 ID 按供应商顺序在已启用目录中匹配；仅有一个供应商时，未知 ID 可透传上游。
- `server.default_model` 或 `--default-model` 填充请求中省略的模型。

能力、思考强度与失败处理见[模型与运行行为](model-behavior.zh-CN.md)。

## 管理接口

以下接口位于 `/api/v1/*`，使用工具访问密钥：

| 接口组 | 用途 |
| --- | --- |
| `/app/version`、`/app/health`、`/app/meta` | 版本、健康、路径、配置标记和数量 |
| `/settings`（`GET`、`PATCH`） | 读取脱敏配置，保存并应用更新 |
| `/providers`、`/provider-forms` | 供应商、账户摘要、凭据字段 |
| `/providers/{id}/accounts`（`POST`） | 校验并追加账户 |
| `/providers/{id}/accounts/{account}`（`DELETE`） | 删除账户 |
| `/stations` | `agg` 预设 |
| `/models`、`/catalog/stream` | 模型能力与增量 SSE 目录 |
| `/models/unavailable/reset`（`POST`） | 清除记忆中的模型禁用状态 |
| `/probe`（`GET`、`POST`） | 读取或开始可用性探测 |
| `/refresh`（`GET`、`POST`） | 读取或执行凭据续期 |
| `/credits`、`/credits/claim`（签到用 `POST`） | 余额、被动配额和签到 |
| `/login/providers`、`/login/start`、`/login/poll`、`/login/input`、`/login/cancel` | 浏览器与设备授权 |
| `/stats` | 日期、模型和最近调用统计 |
| `/playground/*` | 使用工具密钥聊天，不暴露模型 API 密钥 |

运行服务的 `/docs` 与 `/openapi.json` 提供完整字段和方法定义，启用鉴权时需要工具密钥。
仓库 `docs/` 是用户指南，与运行时 Swagger `/docs` 路由分别维护。

OpenAI 推理领域错误使用 `{"error":{"message":...,"type":...,"code":...}}`；
管理错误使用 `{"code":...,"message":...,"detail":...}`；Messages 使用 Anthropic 格式。
非 Messages 路由的安全中间件拒绝使用管理格式，框架请求校验也可能返回自身的 `detail`。
流式响应发出头部和内容后，错误通过流事件传递，不能只根据 HTTP 状态判断结果。
