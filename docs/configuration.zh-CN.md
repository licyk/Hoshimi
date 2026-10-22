# 配置

[文档目录](README.zh-CN.md) | [English](configuration.md)

## 配置文件位置

可以通过渠道与设置页面完成配置。使用文件时，参考
[config.example.json](../config.example.json)，将自己的配置保存到以下命令显示的位置：

```bash
hoshimi config path
```

即使文件尚未创建，命令也会显示绝对路径。所有命令与界面使用同一份文件，
不受启动目录影响。

| 平台 | 默认路径 |
| --- | --- |
| Linux | `~/.local/share/hoshimi/config.json`，或 `$XDG_DATA_HOME/hoshimi/config.json` |
| Windows | `%APPDATA%\hoshimi\config.json` |
| macOS | `~/Library/Application Support/hoshimi/config.json` |

`HOSHIMI_DATA_DIR` 统一迁移全部数据，配置始终为 `<数据目录>/config.json`。
`HOSHIMI_LOG_LEVEL` 控制日志等级。使用 `hoshimi env` 查看应用环境变量。
供应商清单中的环境变量名描述上游约定，不会替代 Hoshimi 的账户配置。

## 旧版迁移

已移除 `--config` 和 `HOSHIMI_CONFIG`，不再读取或自动迁移启动目录中的配置。
升级前请将旧文件手动移到 `hoshimi config path` 显示的位置。

## 最小示例

```json
{
  "server": {
    "access_token": "your-tool-access-key",
    "model_api_key": "your-model-api-key"
  },
  "providers": {
    "nvidia": {"accounts": [{"id": "nvidia-1", "api_key": "your-provider-api-key"}]}
  }
}
```

请替换占位密钥，仅添加需要的供应商。完整示例包含演示凭据，不能直接当作有效账户使用。

## 配置字段

| 字段 | 含义 |
| --- | --- |
| `server.host`、`server.port` | 监听地址，默认 `127.0.0.1:10600`；端口为 `0` 时由系统任选空闲端口 |
| `server.strict_port` | 为 `true` 时，`server.port` 被占用即启动失败，不再寻找其他端口（默认 `false`；`serve --strict-port/--no-strict-port` 可覆盖） |
| `server.access_token` | 管理 API、`/docs`、`/openapi.json` 的工具访问密钥 |
| `server.model_api_key` | 外部 `/v1/*` 推理密钥 |
| `server.default_model` | 请求省略模型时的默认模型 |
| `server.max_body_bytes` | 请求体大小上限 |
| `server.max_account_retries` | 账户重试上限 |
| `server.auto_refresh` | 自动凭据续期 |
| `server.auto_checkin` | 自动每日签到 |
| `server.check_updates` | 启动后及每天向 PyPI 检查是否有新版本，结果显示在概览页（`false` = 不发出请求） |
| `playground.system_prompt` | 作为 Playground 每个对话的第一条消息发送（在 设置 → 常规 中设置）；留空则不发送 |
| `server.stats_keep_days` | 每条调用记录在 `hoshimi.db` 中保留的天数（默认 `90`，至少 `1`）；按日总量和累计计数始终保留 |
| `security.allow_cidrs` | 允许访问的客户端网段 |
| `security.allow_origins` | 跨域来源，要求设置工具访问密钥 |
| `security.allow_hosts` | 允许的 Host 名称 |
| `providers.<id>.enabled` | 是否启用供应商，也可关闭匿名入口 |
| `providers.<id>.accounts` | 账户与凭据列表 |
| `providers.<id>.options` | 供应商特有选项 |

## 上游网络与请求拦截

设置页面提供全局网络配置，以及每个提供商的独立覆盖项。网关的目录加载、
推理、登录、令牌刷新、余额查询和签到使用同一套出口选择规则，例如：

```json
{
  "network": {"mode": "proxy", "proxy": "http://127.0.0.1:7890"},
  "providers": {
    "cline": {"options": {"network": {"mode": "inherit"}, "client_profile": "extension"}},
    "ollama": {"options": {"network": {"mode": "direct"}}}
  }
}
```

- `auto`（默认）：读取 `HTTP_PROXY`、`HTTPS_PROXY`、`ALL_PROXY` 和 `NO_PROXY`，
  同名小写变量优先。设置 `system_proxy: true` 后，在没有选中环境代理时尝试
  Windows 系统代理。
- `direct`：直连。`proxy`：通过 `proxy` 指定 HTTP(S) 或 SOCKS5 代理地址；
  代理失败时不会自动退回直连。
- 提供商默认使用 `inherit`，继承全局配置；显式配置会替代全局策略。
  回环目标始终直连；`NO_PROXY` 只影响自动选择，不覆盖显式指定的代理。

代理地址可包含认证信息，保存在本地配置文件中；设置 API 只返回是否已配置，
CLI 输出会将地址脱敏。界面清空代理时恢复自动选择或继承。
修改出口仅影响后续请求，正在输出的流继续使用原连接。普通 API 请求不保存
响应 Cookie，每次登录使用独立 Cookie 会话。

浏览器授权页面使用浏览器自己的网络设置。Vertex 的 Google Auth HTTP 请求
接入提供商网络策略，models.dev 补充元数据使用全局策略；Bedrock SDK 服务请求接收
选中的 HTTP(S) 代理，但 AWS
凭据发现链仍由 SDK 控制。SOCKS 支持适用于 HTTPX 请求，不适用于 Bedrock SDK
传输层。诊断记录覆盖 HTTPX 请求，不包含 AWS SDK 内部请求。

HTML 拦截页、Cloudflare 挑战、客户端限制、地区限制和签名拒绝会在凭据处理前
分类，不会导致已保存的刷新令牌失效。单独的 HTTP 403 不再被视为凭据过期。
此功能不包含自动解浏览器挑战或模拟 TLS 指纹。

设置页面显示每个提供商各操作最近一次的结果。
管理接口 `GET /api/v1/network/diagnostics` 返回操作、主机、出口来源、时间、
HTTP 状态和错误分类，不返回路径、查询参数、请求头、正文、Cookie 或代理地址。

Cline 使用公开推荐模型接口加载免费目录。
`providers.cline.options.client_profile` 可选 `sdk`（默认，WorkOS 设备码登录）
或 `extension`（浏览器授权，回调端口 48801–48811）。
新凭据会保存所用 profile，已有账号继续使用其登录时的身份；切换选择器影响
后续登录。一次性授权码交换失败后需要重新登录，不自动重放授权码。

成功加载的模型目录保存在 `<数据目录>/catalog-cache`，使用提供商、配置和账号
信息的哈希作为缓存键；文件只包含模型元数据及账号标识，不保存凭据。
启动仅恢复七天内的快照，账号或端点变化后不复用旧配置的快照。
加载失败时保留最近的成功目录，并等待 30 秒再重试。现有目录骤减保护仍保留：
大批模型移除需要第二次成功响应确认。目录 API 同时返回来源、最近成功时间、
错误类别与重试时间；可以删除缓存文件来清除持久化模型元数据。

## 数据目录中的文件

网关持续积累的记录保存在 SQLite 数据库 `<数据目录>/hoshimi.db` 中：用量统计
（逐条调用记录保留 `server.stats_keep_days` 天，按小时总量保留 120 天，按日总量和
累计计数永久保留）、每日签到状态及其历史（保留 365 天）、探测结果，以及
Playground 的对话（删除前一直保留，并记住上次打开的对话）。统计约每秒批量写入一次，
关闭时补写；对话消息在发送时写入。

需要人工或其他程序读取、编辑的内容仍是普通文件：`config.json`、凭据、
`eac-user.json`、`server.json`（运行中服务的地址、实际端口、URL 与 PID）和
`catalog-cache/`。

升级后首次启动时，`stats.json`、`checkin.json` 和 `probe.json` 会导入数据库并
重命名为 `*.migrated`；无法读取的文件重命名为 `*.corrupt`，对应记录从空开始。
停止服务后删除 `hoshimi.db` 即可清除全部记录。

## 端口选择

`hoshimi serve` 自行绑定套接字，因此检测过的端口就是实际服务的端口。
`server.port` 被占用或保留时，依次尝试后续端口直到 65535，再从 1024 回绕到所请求
端口之前；其他绑定错误（例如本机不存在的地址）会立即失败。启用
`server.strict_port`（或 `--strict-port`）时只尝试所请求的端口。实际绑定的端口会
写入日志和 `server.json`，并由 `GET /api/v1/settings` 的 `listening` 返回；
改用其他端口不属于待生效的修改，不会提示重启。

## CLI 修改

```bash
hoshimi config show
hoshimi config get server.port
hoshimi config set server.port 10600
hoshimi config set providers.kilo.enabled false
hoshimi check
```

值会尽可能按 JSON 解析，`null` 表示清空。读取时会遮蔽凭据。
通过 CLI 或直接编辑文件后，需要重启正在运行的服务。

## 界面修改的生效范围

设置保存会同时写文件并更新运行中的网关。渠道添加账户采用追加方式，保存前校验，
并启用对应供应商；删除账户也同步到运行中的账户池。

只有构建输入改变的供应商池会重建。修改其他供应商或服务字段，不会清除无关账户的
限流等待和登录状态；仅修改网络配置也会保留账号状态。提供商 HTTP 客户端保持
可用，使进行中的流式请求能够结束。
被重建供应商的内存账户状态会重新创建，包括 `needs_login`。
监听地址、端口和 `strict_port` 需要重启才能重新绑定；`stats_keep_days` 从下次清理起生效（清理随新记录的调用进行，至多每分钟一次）。

文件采用原子替换和尽可能严格的权限。无效或不可读配置会阻止启动，解析损坏时会
尽可能先备份，不会静默覆盖。

## 局域网访问

例如配置以下字段后重启：

```json
{
  "server": {
    "host": "0.0.0.0",
    "access_token": "your-tool-access-key",
    "model_api_key": "your-model-api-key"
  },
  "security": {"allow_cidrs": ["192.168.1.0/24"]}
}
```

外部推理客户端使用模型 API 密钥，管理功能使用独立的工具访问密钥。
其他来源的浏览器客户端按需加入 `security.allow_origins`；Host 检查需要时配置
`security.allow_hosts`。完整边界见[鉴权](authentication.zh-CN.md)。
