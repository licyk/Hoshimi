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
| `server.host`、`server.port` | 监听地址，默认 `127.0.0.1:10600` |
| `server.access_token` | 管理 API、`/docs`、`/openapi.json` 的工具访问密钥 |
| `server.model_api_key` | 外部 `/v1/*` 推理密钥 |
| `server.default_model` | 请求省略模型时的默认模型 |
| `server.max_body_bytes` | 请求体大小上限 |
| `server.max_account_retries` | 账户重试上限 |
| `server.auto_refresh` | 自动凭据续期 |
| `server.auto_checkin` | 自动每日签到 |
| `security.allow_cidrs` | 允许访问的客户端网段 |
| `security.allow_origins` | 跨域来源，要求设置工具访问密钥 |
| `security.allow_hosts` | 允许的 Host 名称 |
| `providers.<id>.enabled` | 是否启用供应商，也可关闭匿名入口 |
| `providers.<id>.accounts` | 账户与凭据列表 |
| `providers.<id>.options` | 供应商特有选项 |

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
限流等待和登录状态；共享 HTTP 客户端保持不变，使进行中的流式请求能够结束。
被重建供应商的内存账户状态会重新创建，包括 `needs_login`。
监听地址和端口需要重启才能重新绑定。

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
