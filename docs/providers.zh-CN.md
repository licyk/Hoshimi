# 供应商接入

[文档目录](README.zh-CN.md) | [English](providers.md)

## 类型与入口

完整来源、协议、凭据、套餐和移植覆盖见[供应商去重清单](provider-inventory.md)。
运行时注册由 `hoshimi/core/providers/factory.py` 决定，来源记录不等于已实测可用。
账户字段定义见 `hoshimi/core/provider_forms.py` 和 `/api/v1/provider-forms`。

| 类型 | 示例 |
| --- | --- |
| 免费站点和订阅 | Cline、TRAE、ZCode、Qoder、MiniMax Agent、Gemini Code Assist、CodeBuddy、CodeArts、EAC |
| 常规云 API | OpenAI、Anthropic、OpenRouter、NVIDIA、Groq、Mistral、Together、DeepSeek、Fireworks、xAI |
| 云平台 | Azure、Cloudflare、Vertex、Bedrock |
| 特殊协议和分类 | Radius、TypeSafe |
| 本地后端 | LM Studio、Ollama、llama.cpp |
| 通用兼容入口 | `agg`，自定义地址和密钥，或选择 22 个预设 |

不同套餐和协议保留独立 ID：`minimax` 为 Agent 登录；`minimax-api`、`minimax-cn`、
`minimax-chat` 为不同 API 服务；`gemini` 为 Code Assist，`google` 为 Gemini Developer
API，`google-vertex` 为 Vertex；`amazon-bedrock` 和 `bedrock-mantle` 分别配置。
TypeSafe 提供分类，不是聊天模型。

## 原生账户参考

OpenCode 和 Kilo 默认创建匿名账户，可以通过 `enabled: false` 关闭。内置模型是目录
不可用时的历史回退，实际以上游目录和账户权限为准。常规新增云 API 参见
[云平台](cloud-providers.zh-CN.md)、[NVIDIA](nvidia.zh-CN.md) 和生成清单。

| ID | 上游 | 账户字段 | 续期 | 内置回退模型 |
| --- | --- | --- | --- | --- |
| `cline` | `api.cline.bot` | `access_token`, `refresh_token` | 支持 (workos) | `stealth/space-bunny-alpha`, `cline-free/*` |
| `codex` | ChatGPT/Codex OAuth + Responses | `access_token`, `refresh_token`, `account_id`, `client_id`, `expires_at` | 支持 | 账户目录或显式声明模型 |
| `lobsterai` | `lobsterai-server.youdao.com` | `access_token`, `refresh_token`, `uuid` | 支持 | `deepseek-v4-flash`, `MiniMax-M3`, ... |
| `loomy` | `loomyad.xunfei.cn` | `access_token` （也发送为 `token` 头部） | 不支持 | `deepseek-v4-flash-0731`, `spark-x`, ... |
| `buddy` (CodeBuddy) | `copilot.tencent.com` | `access_token`, `refresh_token` | 支持 | 16 (hy4/hy3, deepseek-v4, glm-5.x, kimi-k2/3, minimax-m3) |
| `workbuddy` | `www.workbuddy.ai` | `access_token`, `refresh_token` | 支持 | 23 (gpt-5.x, gemini-3.5-flash, glm/kimi, ...) |
| `qoder` | `openapi.qoder.sh` | `access_token`, `security_oauth_token`, `refresh_token`, `machine_id` | 支持 | `auto`, `qmodel_38max`, `gmodel`, ... |
| `qodercn` | `openapi.qoder.com.cn` | 同 `qoder` | 支持 | 同 `qoder` |
| `trae` | `trae-api-cn.mchost.guru` | `access_token`, `refresh_token`, `device_id`, `machine_id`, `uid` | 支持 | `glm-5.2`, `DeepSeek-V4-*`, `kimi-k*`, ... |
| `raccoon` | `xiaohuanxiong.com` | `access_token`, `refresh_token`, `device_id`, `office_identity` | 支持 | `sn-glm-5-3`, `sn-sensenova-6-8-*`, ... |
| `minimax` | `agent.minimax.cn` | `access_token`, `refresh_token` | 支持 | `MiniMax-M3.1-Flash-Preview`, `MiniMax-M3`, ... |
| `zcode` | `zcode.z.ai` | `zcode_jwt` (or `access_token`), `device_mid` | 不支持 | `GLM-5.3-Flash`, `GLM-5.3` |
| `opencode` | `opencode.ai` | `api_key`, `project_id` （匿名入口无需填写） | 不支持 | `big-pickle`, `mimo-v2.6-flash-free`, ... |
| `kilo` | `api.kilo.ai` | 可选 API Key 或设备登录；默认匿名免费池 | 不支持 | 16 个历史免费 ID (`openrouter/free`, `kilo-auto/free`, ...) |
| `gemini` | `daily-cloudcode-pa.googleapis.com` | `access_token`, `refresh_token` （options 中配置 `client_id`/`client_secret`） | 支持 | `gemini-3.8-flash`（思考菜单 low / medium / high / tiered） |
| `codearts` | ModelArts `snap-access.cn-north-4` | `access_key_id`, `secret_access_key`, `security_token` | 不支持 | `GLM-5.x`, `deepseek-v4*`, `openpangu-2.0-*`, ... |
| `eac` | 内置或自定义合租 relay | 首次登录使用 `relay: "bundled"`; 自定义 `base_url` + `access_token` （direct） or `signing_secret` （worker）; `user_token` = 本安装的 GitHub 授权 （从 `eac-user.json` 自动加载） | 不支持 | `deepseek-ai/deepseek-v4.1-flash`, `moonshotai/kimi-*`, `z-ai/glm-5.3*`, `openai/gpt-oss-20b`, 以及两个转发免费 ID (`opencode/deepseek-v4.1-flash-free`, `opencode/glm-5.3-flash-free`) |
| `agg` | 任意 OpenAI 兼容站点 (`base_url`) | `base_url` + `api_key` （或 `access_token`/`token`）; 可选 `models`、`headers`、`options` | 不支持 | 站点 `/models` 或账户 `models` 列表 |
| `zhipu` | `open.bigmodel.cn/api/paas/v4` | `api_key` （来自 bigmodel.cn 控制台） | 不支持 | 7 个历史免费回退 ID (`glm-4.7-flash`, `glm-z1-flash`, `glm-4.6v-flash`, ...) |

## 通用兼容入口 agg

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

每个账户代表一个站点，`base_url` 为 OpenAI API 根地址，也可填写 `preset`。
显式地址优先；`hoshimi stations` 列出预设，上游环境变量名仅用于说明，不自动读取密钥。
凭据支持 `api_key`、`access_token`、`token`。缺地址、未知预设或缺密钥的账户在启动时
会告警并丢弃；界面添加前会校验。

账户 `models` 声明目录并限制这些 ID 对应的账户选择；省略时读取真实目录。
站点任务筛选按地址匹配，未知元数据保留；LLM7 使用独立规则，不添加 pi-free 的
静态路由选择器。免费标记保持免费、付费和未知三态。
具体差异见 [pi-free 兼容说明](pi-free.zh-CN.md)。

账户 `options` 覆盖供应商默认选项，可声明 `vision`、`reasoning`、`context_window`、
`max_output`，也可在 `model_specs` 按 ID 覆盖。思考菜单使用 `efforts`、`effort_patch`、
`effort_off_patch`。CommandCode 的站点能力表优先于默认值，账户声明优先于站点表；
未声明能力时只按已有证据补充，不凭空生成思考菜单。
账户 `headers` 可增加上游头部，但不能替换 Authorization。

## Zhipu

```json
{
  "providers": {
    "zhipu": {"accounts": [{"id": "zhipu-1", "api_key": "your-bigmodel-api-key"}]}
  }
}
```

默认地址为 `https://open.bigmodel.cn/api/paas/v4`，`options.base_url` 可改为国际站
`https://api.z.ai/api/paas/v4`。目录优先实时获取，失败时回退七个历史免费 ID：
`glm-4.7-flash`、`glm-4.5-flash`、`glm-4-flash-250414`、`glm-z1-flash`、
`glm-4.6v-flash`、`glm-4.1v-thinking-flash`、`glm-4v-flash`。
可用范围仍由上游和额度决定。无菜单的文本模型保留调用方字段；明确为思考模型的
Z1 与 V-Thinking 使用预算阶梯。

## 本地后端

`lmstudio`、`ollama`、`llamacpp` 只有显式添加账户且启用后才连接，不扫描本地端口。
本地服务无需鉴权时可以省略密钥。例如：

```json
{
  "providers": {
    "ollama": {"accounts": [{"id": "local", "base_url": "http://127.0.0.1:11434/v1"}]}
  }
}
```

LM Studio 使用已加载上下文并做粗略超限检查，Ollama 读取原生能力目录，llama.cpp
将思考控制映射到模板参数。本地模型不会自动标记免费。

## 供应商选项与限制

- TRAE：`channel`（默认 `solo_work_lite`）、`user_agent`、`app_id`、`ide_version`、
  `ide_version_code`、`os_version`、`device_brand`。
- ZCode：`app_version`（默认 `3.14.3`）。
- CodeArts：`benefit_models`，默认包含 `glm-5.3-flash` 和 `deepseek-v4.1-flash`；
  优惠请求头被拒绝后会去掉重试。
- Gemini Code Assist：`client_id`、`client_secret` 用于 OAuth 续期。
- Zhipu：`base_url` 用于切换站点。

Qoder 参考实现使用加密 WASM 推理路径，普通公开 Chat 端点可能返回 `503`。
头部、模型、刷新行为一致不代表已验证实时推理完全一致。
登录、续期与 EAC 见[鉴权](authentication.zh-CN.md)，其他专项见
[云平台](cloud-providers.zh-CN.md)、[NVIDIA](nvidia.zh-CN.md)、
[OpenCode](opencode-integration.zh-CN.md)。
