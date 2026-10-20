# OpenCode 集成

[文档目录](README.zh-CN.md) | [English](opencode-integration.md)

## 匿名与密钥访问

未显式关闭时，`opencode` 默认创建匿名账户，免费目录取上游 `-free` 切片。
渠道中添加 API Key 账户可使用鉴权目录，但仍受密钥权限限制。
使用 `opencode/<model-id>` 显式路由，见 [API](api.zh-CN.md#模型路由)。

## 协议行为

实现参考外部 dsh-our-free-model 的 `src/upstream.js`，快照 `f0b979f`。
该信息标明移植基线，不表示刚刚完成上游实测。

- 原生适配器使用 `/inference/v1`、`user-agent: opencode/latest/2.0.26/cli`、
  `x-opencode-client: cli`，不发送旧的 `x-opencode-request`。
- `x-opencode-session`、`x-opencode-session-id`、`x-session-affinity`、
  `x-session-id` 一致。会话 ID 使用降序编码，按账户和对话稳定生成，来源依次为
  `session_id`、`conversation_id`、`user` 或首条用户消息。相同开场的独立对话请发送
  不同 `session_id`。
- 匿名请求声明参考 v2 的 read/shell 工具。调用方同名工具会在声明、历史和强制选择中
  一致改名，响应再还原。注入工具标为占位，调用未授权工具会显式报错。
- 上游请求省略 `tool_choice: none`，响应守卫仍执行该约束。API Key 账户保留原工具。
- `/inference/v1` 下仍按模型选择 Chat、Responses、Messages 协议。

实时目录优先，失败保留旧缓存，只有冷启动使用历史回退。新免费 ID 保留上游原值。
DeepSeek V4.1 Flash、GLM 5.3 Flash、Nemotron Super VL、Space Bunny 使用参考声明的
思考菜单覆盖。

## 验证范围

`tests/core/test_opencode_lane.py`、`tests/core/test_pi_free_parity.py` 和协议测试描述
本地回归覆盖，不能替代特定网络出口的成功请求。可用性、地区和额度以当前响应或
[显式探测](model-behavior.zh-CN.md#可用性探测)为准。
