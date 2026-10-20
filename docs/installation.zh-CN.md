# 安装与 Web UI

[文档目录](README.zh-CN.md) | [English](installation.md)

## 安装与升级

需要 Python 3.10 或更高版本：

```bash
python -m pip install -U hoshimi
hoshimi serve
```

打开 `http://127.0.0.1:10600`。发行包已包含构建好的 Web UI，普通用户无需
Bun 或自行构建前端。`python -m hoshimi` 与 `hoshimi` 等价。
源码安装提供界面前需要构建，见[开发说明](development.zh-CN.md)。

默认启用 OpenCode Zen 和 Kilo 匿名入口，无需配置文件或账户。实际模型取决于
上游目录、额度和服务状态；添加密钥后可能展示不同目录。通过渠道页面添加其他
账户，在设置中关闭不需要的供应商。

## 可选云平台依赖

```bash
python -m pip install -U "hoshimi[google]"   # Vertex ADC / 服务账户
python -m pip install -U "hoshimi[bedrock]"  # Bedrock SDK 与 Bearer 事件流
```

同时使用可安装 `"hoshimi[google,bedrock]"`。Bedrock 的 Bearer 模式也需要此扩展解析
事件流，其他普通 HTTP API 接入无需这些 SDK。具体账户和协议见
[云平台](cloud-providers.zh-CN.md)。

## 常用命令

| 命令 | 用途 |
| --- | --- |
| `hoshimi serve` | 启动 API 和 Web UI |
| `hoshimi check` | 检查配置，不启动服务 |
| `hoshimi models` | 模型列表 |
| `hoshimi providers` | 供应商列表 |
| `hoshimi stations` | `agg` 预设列表 |
| `hoshimi login [provider]` | 列出支持登录的供应商，或开始授权 |
| `hoshimi version` | 应用与组件版本 |
| `hoshimi env` | 环境变量 |
| `hoshimi config show/get/set/path` | 查看与修改配置 |

使用 `<命令> --help` 查看参数。

## 服务参数

| 参数 | 用途 |
| --- | --- |
| `--host`、`--port` | 监听地址和起始端口；端口 `0` 自动选择空闲端口 |
| `--strict-port/--no-strict-port` | 端口占用时报错，或尝试下一个端口 |
| `--open/--no-open` | 是否打开浏览器 |
| `--ui/--no-ui` | 提供管理界面，或仅提供 API |
| `--access-token` | 工具访问密钥 |
| `--model-api-key` | 外部推理密钥 |
| `--default-model` | 请求省略模型时使用的默认值 |
| `--refresh/--no-refresh` | 自动续期凭据 |
| `--checkin/--no-checkin` | 自动每日签到 |

默认端口被占用时会尝试下一个端口，以终端实际打印的 URL 为准。
需要固定地址时使用 `--strict-port`。配置保存和旧版迁移见
[配置](configuration.zh-CN.md)，远程访问规则见[鉴权](authentication.zh-CN.md)。

## 界面页面

| 页面 | 功能 |
| --- | --- |
| 概览 | 健康状态、API 入口和供应商概况 |
| 模型 | 搜索、能力、免费标记、可用性探测和目录重试 |
| 渠道 | 账户、登录授权、过期时间、续期、余额、签到和删除 |
| 用量 | 日期和模型统计、首字延迟、生成速度及最近请求 |
| Playground | 流式聊天、模型和思考强度选择 |
| 设置 | 服务参数、独立密钥、网络策略、供应商和外观 |

语言跟随系统，支持英文、中文和日文。主题、强调色和已验证的工具访问密钥保存
在 localStorage。Playground 的自动模式不指定思考档位。

模型与 Playground 共享增量 SSE 目录：先展示缓存，再按供应商独立更新。
刷新失败保留最近成功目录，并允许重试；标准 `/v1/models` 仍返回 JSON。
详细规则见[模型与运行行为](model-behavior.zh-CN.md)。
