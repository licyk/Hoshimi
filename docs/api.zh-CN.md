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
| `/app/update` | PyPI 上是否有新版本：`status`（`checking`、`available`、`current`、`failed`、`disabled`）和 `latest` |
| `/settings`（`GET`、`PATCH`） | 读取脱敏配置，保存并应用更新；`restart_required` 表示已保存的监听地址/端口需重启生效；`listening` 为实际绑定的套接字（`port`、`requested_port`，请求端口被占用而改用其他端口时 `fallback` 为真，这不算需要重启） |
| `/providers`、`/provider-forms` | 供应商、账户摘要、凭据字段 |
| `/channels` | 全部渠道及其账户、续期、冷却、被动配额、网络诊断、今日用量、最近五次签到结果（`recent_checkins`）与状态（仅读本地状态，不请求上游） |
| `/providers/{id}/accounts`（`POST`） | 校验并追加账户 |
| `/providers/{id}/accounts/{account}`（`DELETE`） | 删除账户 |
| `/stations` | `agg` 预设 |
| `/models`、`/catalog/stream` | 模型能力与增量 SSE 目录 |
| `/models/unavailable/reset`（`POST`） | 清除记忆中的模型禁用状态 |
| `/probe`（`GET`、`POST`） | 读取或开始可用性探测 |
| `/refresh`（`GET`、`POST`） | 读取或执行凭据续期 |
| `/credits`、`/credits/claim`（签到用 `POST`） | 余额、被动配额和签到 |
| `/credits/checkins?provider=&limit=50` | 签到历史，新的在前（含每日自动签到与手动领取，保留 365 天） |
| `/login/providers`、`/login/start`、`/login/poll`、`/login/input`、`/login/cancel` | 浏览器与设备授权 |
| `/stats?range=24h\|day\|7d\|30d\|90d`（`GET`，`DELETE` 清空） | 区间总量、补零序列、延迟分位数，以及按模型、供应商、账户、来源的拆分。`range=day&date=YYYY-MM-DD` 为本地时间的某一自然日，按小时展示（夏令时切换日为 23 或 25 点；默认今天；未来或格式错误的日期返回 400），与前一天比较——若为今天，只比较前一天同一时刻之前的部分。报告包含 `since`/`until`（不含）、`date`、`hourly`（某天只有按日总量时为假，例如导入的旧数据或超出小时保留期）和 `server_time` |
| `/stats/days` | 每个有用量的本地日期（`date`、`calls`、`tokens`、`failed`），旧的在前，另含 `first` 与 `today`，供日历使用 |
| `/stats/calls` | 保留期内的全部调用记录，新的在前；支持 `limit`、`before` 游标，`model`/`provider`/`account`/`origin`/`ok` 筛选，`since`/`until`（带时区的 ISO 8601，`until` 不含）以及 `q`（在模型、供应商、账户、错误代码/信息、结束原因中不区分大小写地搜索） |
| `/stats/live` | SSE：先发送进行中调用的快照，再推送 `started`/`progress`/`finished` 事件 |
| `/playground/*` | 使用工具密钥聊天，不暴露模型 API 密钥；请求没有 system 消息时，先发送 `playground.system_prompt` |
| `/playground/conversations` | 保存的对话，每个对话是一棵消息树：列出（`?archived=true` 为归档；临时对话不会列出）、创建（`temporary`）；`/{id}` 获取、`PATCH`（标题、模型、推理强度、归档，`current_leaf` 切换版本，`tools_off` 为该对话不使用的工具，`full_access` 让其工具无需询问即可运行）、`DELETE` |
| `/playground/conversations/{id}/messages` | 在 `parent_id` 之后发送问题（编辑即在被编辑消息的父消息之后发送），以 Server-Sent Events 流式返回回复：`user`、`assistant`，然后是 `stage`、`notice`、`reasoning`（模型展示的推理内容）、`delta`，最后 `done`；`/{message_id}/retry` 流式生成另一条回复；`/{message_id}/request` 返回该回复对应的 Chat Completions 请求。启用工具（`playground.tools`）时，回复是一个智能体循环：`tool` 事件携带每次调用的最新状态（`pending`、`waiting` 等待批准、`running`、`done`、`error`、`denied`、`stopped`），保存的回复按顺序记录 `steps`（推理、文本和工具调用）。没有其他内容时，每 2 秒发送一次 `: ping` 注释 |
| `/playground/conversations/{id}/approvals/{call_id}`（`POST`） | 答复等待批准的调用：`{"decision": "allow" \| "always" \| "deny"}`（`always` 在服务重启前对此对话中的该工具生效） |
| `/playground/conversations/{id}/terminals` | 该对话的工具启动的后台命令；`DELETE /{session_id}` 停止其中一个及其启动的进程。删除对话或停止服务时也会停止 |
| `/playground/conversations/{id}/branch`、`/{id}/title` | 截至某条消息分支出新对话；由模型为对话命名（手动设置的标题会保留） |
| `/playground/attachments` | 上传图片（PNG、JPEG、WebP、GIF）或以 UTF-8 文本上传文件（`?name=`，`?type=` 为文件原类型）；`GET`/`DELETE /{id}` |
| `/playground/session` | `GET`/`PUT` 上次打开的对话，Playground 启动时会重新打开它 |

运行服务的 `/docs` 与 `/openapi.json` 提供完整字段和方法定义，启用鉴权时需要工具密钥。
仓库 `docs/` 是用户指南，与运行时 Swagger `/docs` 路由分别维护。

OpenAI 推理领域错误使用 `{"error":{"message":...,"type":...,"code":...}}`；
管理错误使用 `{"code":...,"message":...,"detail":...}`；Messages 使用 Anthropic 格式。
非 Messages 路由的安全中间件拒绝使用管理格式，框架请求校验也可能返回自身的 `detail`。
流式响应发出头部和内容后，错误通过流事件传递，不能只根据 HTTP 状态判断结果。
