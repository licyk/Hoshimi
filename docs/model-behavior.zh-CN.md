# 模型与运行行为

[文档目录](README.zh-CN.md) | [English](model-behavior.md)

## 目录发现与缓存

上游目录决定模型 ID，供应商能力表和账户声明补充能力。缓存按账户池隔离，替换账户池
不会继承其他池的目录。分页发现完整成功后才发布结果，刷新失败保留最近成功目录。

模型与 Playground 共享 `GET /api/v1/catalog/stream`：先返回缓存，再按供应商独立
推送 SSE。最多八个发现任务并发，多个客户端共享进行中的任务。每轮等待 20 秒后，
未完成供应商标记超时，保留已收到的模型并允许重试。仅加载供应商元数据不会请求上游
目录。标准 `/v1/models` 始终返回 JSON。

五分钟模型缓存同时防止异常缩减：新目录不足旧目录一半时，暂缓一个周期；下一次 TTL
后上游仍确认缩减才接受。空目录或发现失败使用的静态回退列表不受此规则拦截。
回退 ID 是历史恢复数据，不承诺当前可用。

## 能力来源

供应商能力表和账户 `options`、`model_specs` 声明优先；已实现的原生目录可补充能力及
端点限制。通用声明包括 `vision`、`reasoning`、`context_window`、`max_output`、
`efforts`、`effort_patch`、`effort_off_patch`。

models.dev 仅补充未声明 ID 的视觉与思考能力，不提供上下文、输出上限或思考菜单，
因为这些取决于端点、套餐和协议。内置快照支持离线使用，后台线程更新后续查询，
请求路径不等待；失败保留已有信息。不引入第三方基准排名，也不暗中替换用户指定的模型。

`free` 保留 `true`、`false`、`null`（未知），表示上游访问和价格证据，不承诺无限额度。
本地模型不会自动免费。站点筛选与免费状态差异见 [pi-free](pi-free.zh-CN.md)。

## 思考强度

以下策略用于转换请求。原生 Anthropic Messages 保留调用方思考、输出配置和 Token
预算，由上游校验，不再经过 Chat 强度策略重写。

只有显式传入 `reasoning_effort` 或 `reasoning.effort` 才应用强度策略。
未选择档位时策略不修改请求体，但供应商协议转换仍可能应用自身默认值。
Playground 自动模式不发送档位；调用方已有 Token 限制参与预算计算。

| 能力 | 显式档位的处理 |
| --- | --- |
| 声明思考菜单 | 按站点档位应用 JSON 合并补丁 |
| 支持思考但无菜单 | 预算阶梯：light 2,048，balanced 8,192，deep 为模型输出容量 |
| 不支持思考 | 不进行强度驱动的修改 |

预算阶梯中 low 对应 light，medium 对应 balanced，high 对应 deep。
不能关闭思考的模型将阶梯预算加倍，因为思考和答案共享输出上限；最终仍受模型容量及
调用方更小的上限约束，但阶梯最低为 512 Token，因此显式选择阶梯可能提高小于 512
的调用方限制；原生菜单预算最低为 1 Token。

不同站点使用不同方言：Kilo 使用 `reasoning` 对象，MiniMax 与 ZCode 使用
`output_config.effort`，CodeArts 使用 `thinking.type`，Raccoon 使用
`extra_body.thinking`，CodeBuddy、Cline、Loomy、Qoder 使用各自声明的
`reasoning_effort`。Gemini Code Assist 展示单个 `gemini-3.8-flash`，菜单为
low、medium、high、tiered，转换器据此生成预算和上游模型后缀。
TRAE 有上下文表，但未获取账户级远程思考菜单，因此保留调用方强度字段。
具体以管理模型接口公布的菜单和预算为准。

## 可用性探测

`POST /api/v1/probe` 开始低并发后台探测，`GET` 读取状态。结果附在管理模型行，
并保存到数据目录 `probe.json`。默认探测 OpenCode，Kilo 免费目录已由其上游筛选。
指定其他 ID 时传入：

```json
{"models": ["kilo/<model-id>", "cline/<model-id>"]}
```

| 状态 | 含义 |
| --- | --- |
| `available` | 模型产生增量或 Token |
| `region-blocked` | 当前出口地区被拒绝 |
| `unavailable` | 明确针对模型的拒绝 |
| `throttled` | 配额阻止请求，未实际验证模型 |
| `unknown` | 鉴权、网络或服务端失败，没有模型结论 |

只有明确的模型不可用结果会使探测对象退出可用列表；地区、限流和未知不会判定模型下线。

## 模型失败记忆

真实请求中的 `404`、`410`，以及 `400`、`422` 中明确指出模型已移除的正文，可为
`(供应商, 模型)` 记录一次失败。账户或额度抱怨会阻止歧义文本被误判；`401`、`429`、
`5xx` 不会仅因正文提到模型而记入。

记录十分钟后过期；窗口内三次失败升级为本次运行期间的禁用。禁用中的 ID 返回
`503 model_unavailable`，不请求上游。管理行标记不可用，但失败记忆不一定从目录移除
该行。过期后成功请求可以清除记录；调用 `POST /api/v1/models/unavailable/reset`
或重启可全部清除。该表仅在内存中，与持久化探测结果分开。

## 账户轮换与重试

| 失败 | 处理 |
| --- | --- |
| `401`、`403` | 视情况处理或续期凭据，再换账户 |
| `402`、`429`、`5xx`、Cloudflare `521`–`527`、Anthropic `529` | 可轮换账户，受 `server.max_account_retries` 限制 |
| `404`、`410` | 尝试账户池后记忆模型失败 |
| `400`、`405`、`406`、`415`、`418`、`422`、`451` | 通常属于调用方请求，不耗尽账户池重复尝试 |

正文可进一步区分额度和模型问题，未知状态按可能属于单账户处理。
CodeBuddy `429` 中 `14003` 且仅提供 `SWITCH_MODEL` 时，视为模型拥堵，不轮遍账户；
`403` 的 `11140` 则将受影响账户暂停 30 分钟并轮换，不做无效续期。

配额暂停优先使用上游重置时间及其 UTC 偏移，缺失时默认一小时。暂停状态仅在内存，
通过 credits 接口可见。先轮换，再等待；暂时性失败最多等待重试两次，每次最多八秒。
`Retry-After` 优先于正文时间，显式零值表示立即重试，推测延迟使用完整随机抖动。
凭据拒绝、额度耗尽和模型移除不靠等待修复。**开始输出内容后不会自动重放请求。**

## 余额、签到与被动配额

`GET /api/v1/credits` 查看余额、暂停与签到状态，`POST /api/v1/credits/claim`
手动签到。启用 `server.auto_checkin` 时，启动短暂延迟后每日串行检查一次，结果存入
`checkin.json`。没有对应端点的供应商直接跳过。

响应头可提供不增加请求的被动配额，按以下顺序采用首个可解析的成对值：

| 剩余头部 | 上限头部 |
| --- | --- |
| `x-ratelimit-remaining-requests` | `x-ratelimit-limit-requests` |
| `x-ratelimit-remaining` | `x-ratelimit-limit` |
| `ratelimit-remaining-requests` | `ratelimit-limit-requests` |
| `ratelimit-remaining` | `ratelimit-limit` |
| `x-ratelimit-remaining-requests-day` | `x-ratelimit-limit-requests-day` |
| `x-ratelimit-remaining-day` | `x-ratelimit-limit-day` |

不可解析的成对头部计入解析漂移。各供应商累计响应数、`401/403`、`429`、`5xx` 四类
计数。五分钟后读数标为过期，百分比限制在 0–100。渠道中剩余不超过 10% 为红色，
不超过 25% 为琥珀色，过期数据为琥珀色并提示旧读数。管理接口仅返回头部名称，
不返回原始值。被动统计仅在内存，观察失败不会中断生成。

## 用量统计

`GET /api/v1/stats?range=24h|7d|30d|90d` 返回一个区间：总量、上一个等长区间、补零的
按小时（24h）或按日序列、首字延迟均值/p50/p95、生成速度，以及按模型、供应商、账户、来源的
拆分，并附长期累计与请求/失败数。`GET /api/v1/stats/calls` 分页读取最近 1000 次调用
（新的在前，`before` 游标），含实际应答账户、尝试次数、缓存读写、结束原因和简短错误。
`GET /api/v1/stats/live` 推送进行中的调用；`DELETE /api/v1/stats` 清空全部统计。

`stats.json`（第 2 版，第 1 版文件会原地迁移）跨重启保存，最多每两秒写一次，关闭时补写。
小时桶保留 72 小时，日期桶保留 120 天，长期累计保留。缓存写入读取
`prompt_tokens_details.cache_write_tokens`（或 `cache_creation_tokens`、Anthropic 的
`cache_creation_input_tokens`）；DeepSeek 的 `prompt_cache_hit_tokens` 计为缓存读取。

Chat、Responses、Messages、分类和 Playground 每次请求各记一行；路由失败计为失败，读者断开连接计为
已服务而非网关失败。记录实际强度与来源。解码窗口不足 250 毫秒或速度超过每秒
250 Token 时，不计入均值并返回 `null`，避免将不可测值展示为高速结果。
