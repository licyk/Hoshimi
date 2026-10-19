# Hoshimi

[English](README.md) | [简体中文](README.zh-CN.md)

探索星辰，连接不同的模型。

Hoshimi 将免费模型站点、云服务和本地模型接入同一个网关，提供 OpenAI 和 Anthropic
兼容接口，以及用于管理供应商、账户和模型的 Web UI。

- **统一接口**：支持 Chat Completions、Responses 和 Anthropic Messages，可接入聊天客户端和 Agent。
- **流式输出与工具调用**：统一处理文本、思考内容、工具调用、用量和错误。
- **账户管理**：支持 API Key、浏览器登录和设备授权，并提供凭据刷新、账户轮换、余额及签到功能。
- **模型目录**：展示能力、思考选项和可用性，按供应商独立加载，目录刷新失败时保留缓存。
- **调用统计**：按日期和模型查看 Token 用量、首字延迟、生成速度及最近请求。
- **独立密钥**：工具访问密钥保护管理功能，模型 API 密钥保护外部推理请求。
- **多语言界面**：支持中文、英文和日文，以及主题和强调色设置。

## 安装与运行

需要 **Python 3.10 或更高版本**。从 PyPI 安装或升级，然后启动：

```bash
python -m pip install -U hoshimi
hoshimi serve
```

在浏览器中打开 `http://127.0.0.1:10600`。PyPI 包已包含构建好的 Web UI，
使用时无需安装 Bun 或自行构建前端。

默认启用 OpenCode Zen 和 Kilo 的匿名免费入口，无需配置即可启动。
实际可用模型由上游目录、额度和服务状态决定。可以在设置中关闭不需要的供应商，
或在渠道页面添加其他供应商的账户。

`python -m hoshimi` 与 `hoshimi` 命令等价。常用命令：

```bash
hoshimi check       # 检查配置，不启动服务
hoshimi models     # 查看模型列表
hoshimi providers  # 查看供应商列表
hoshimi version    # 查看版本
hoshimi env        # 查看支持的环境变量
hoshimi config path
```

`hoshimi serve` 支持 `--host`、`--port`、`--strict-port`、`--open/--no-open`、
`--ui/--no-ui`、`--access-token`、`--model-api-key` 和 `--default-model`。

### 可选云平台依赖

Vertex 的 ADC、服务账户鉴权，以及 Bedrock 的 AWS SDK 模式需要额外依赖：

```bash
python -m pip install -U "hoshimi[google]"   # Vertex ADC / 服务账户
python -m pip install -U "hoshimi[bedrock]"  # AWS 凭据链 / ConverseStream
```

同时使用时可以安装 `"hoshimi[google,bedrock]"`。普通 HTTP API 接入不需要这些 SDK；
具体配置见[云平台与特殊协议](docs/cloud-providers.md)。

## Web UI

`hoshimi serve` 默认在同一个地址提供管理界面和 API，使用 `--no-ui` 可仅启动 API。

| 页面 | 功能 |
| --- | --- |
| 概览 | 服务状态、API 入口和供应商概况 |
| 模型 | 搜索模型、查看能力、免费标记和可用性探测结果 |
| 渠道 | 添加账户、登录授权、查看余额、刷新凭据、签到和删除账户 |
| 用量 | 按日期和模型查看 Token、延迟、速度及最近调用 |
| Playground | 流式对话、选择模型和思考强度 |
| 设置 | 服务地址、两种密钥、安全策略、供应商启用状态和外观 |

模型目录按供应商通过 SSE 独立加载：先展示缓存，再逐项更新。
单个供应商超时或失败不会阻止其他供应商展示；失败的目录刷新保留已有缓存，支持重试。
标准 `/v1/models` 仍返回普通 JSON，供 SDK 和客户端使用。

## API 与访问密钥

默认地址为 `http://127.0.0.1:10600`。

| 接口 | 用途 |
| --- | --- |
| `POST /v1/chat/completions` | OpenAI Chat Completions，默认流式输出，可设置 `stream: false` |
| `POST /v1/responses` | OpenAI Responses |
| `POST /v1/messages` | Anthropic Messages，默认 JSON，设置 `stream: true` 使用 SSE |
| `POST /v1/messages/count_tokens` | 本地输入 Token 估算，结果明确标为估算值 |
| `GET /v1/models` | 可用模型目录 |
| `GET /v1/classifiers`、`POST /v1/classify` | TypeSafe 分类能力 |
| `/api/v1/*` | 设置、供应商、账户、模型目录、登录和统计等管理接口 |
| `GET /health` | 无需密钥的健康检查 |
| `/docs`、`/openapi.json` | API 文档与 OpenAPI 定义 |

两种密钥在设置中分别配置：

- **工具访问密钥**（`server.access_token`）：保护管理 API 和 API 文档。
  浏览器需要鉴权时会提示输入，验证后保存到当前浏览器。
- **模型 API 密钥**（`server.model_api_key`）：保护外部客户端调用的 `/v1/*` 接口，
  使用 `Authorization: Bearer <model-api-key>` 或 `x-api-key` 传入。

两种密钥独立生效；某一项留空，只关闭对应接口的鉴权。界面静态页面和健康检查保持公开。
Playground 使用工具密钥访问管理接口，无需在浏览器中另行输入模型 API 密钥。
设置接口不返回密钥原文。供应商自身的 API Key 则配置在对应账户中。

### OpenAI 兼容调用

先查询目录，从响应中复制一个模型 ID，替换下例中的 `<model-id>`：

```bash
curl http://127.0.0.1:10600/v1/models

curl http://127.0.0.1:10600/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"<model-id>","messages":[{"role":"user","content":"你好"}]}'
```

如果已配置模型 API 密钥，需要为上述两个请求增加：

```text
Authorization: Bearer <model-api-key>
```

客户端使用 OpenAI 兼容接口时，Base URL 通常设置为 `http://127.0.0.1:10600/v1`。
模型 ID 保留上游原值；使用 `供应商ID/模型ID` 可明确指定供应商，
也可以使用裸模型 ID 在提供该模型的供应商之间路由。
省略模型时可通过 `server.default_model` 设置默认值。

Responses 当前不支持 `previous_response_id` 和结构化输出 `text.format`，
这些参数会返回 `400`。

### Anthropic 与 Agent

Anthropic 兼容客户端的 Base URL 设置为 `http://127.0.0.1:10600`，
模型选择 `/v1/models` 中的 ID，鉴权使用网关的模型 API 密钥。

```bash
curl http://127.0.0.1:10600/v1/messages \
  -H "x-api-key: <model-api-key>" \
  -H "anthropic-version: 2023-06-01" \
  -H "Content-Type: application/json" \
  -d '{"model":"<model-id>","max_tokens":1024,"messages":[{"role":"user","content":"你好"}]}'
```

工具调用、思考签名回放和 Token 估算的细节见 [Anthropic Agent 接入](docs/anthropic-api.md)。

## 配置

可以直接在渠道和设置页面完成配置。使用配置文件时，参考
[config.example.json](config.example.json)，将自己的配置保存为数据目录下的 `config.json`。
运行 `hoshimi config path` 可以查看绝对路径，文件尚未创建时也会显示。
所有命令和界面均使用同一个文件，与启动时所在的目录无关。

| 平台 | 默认配置文件 |
| --- | --- |
| Linux | `~/.local/share/hoshimi/config.json`，设置 `XDG_DATA_HOME` 后为 `$XDG_DATA_HOME/hoshimi/config.json` |
| Windows | `%APPDATA%\hoshimi\config.json` |
| macOS | `~/Library/Application Support/hoshimi/config.json` |

通过 `HOSHIMI_DATA_DIR` 可以统一更改数据目录，配置文件始终位于 `<数据目录>/config.json`。
**破坏性变更：** 已移除 `--config` 和 `HOSHIMI_CONFIG`，不再读取或自动迁移启动目录中的旧配置。
升级前请将原有配置手动移至 `hoshimi config path` 显示的位置。

例如，配置两种网关密钥并添加 NVIDIA 账户：

```json
{
  "server": {
    "host": "127.0.0.1",
    "port": 10600,
    "access_token": "your-tool-access-key",
    "model_api_key": "your-model-api-key"
  },
  "providers": {
    "nvidia": {
      "accounts": [
        {"id": "nvidia-1", "api_key": "your-nvidia-api-key"}
      ]
    }
  }
}
```

CLI 也支持查看和修改配置：

```bash
hoshimi config show
hoshimi config get server.port
hoshimi config set server.port 10600
hoshimi config path
```

在界面中保存的配置会应用到运行中的网关；直接修改文件或通过 CLI 修改后应重启服务。
配置写入采用原子替换，配置损坏时会备份并拒绝启动，避免静默覆盖。

| 配置项 | 含义 |
| --- | --- |
| `server.host`、`server.port` | 监听地址，默认 `127.0.0.1:10600` |
| `server.access_token` | 工具访问密钥 |
| `server.model_api_key` | 模型 API 密钥 |
| `server.default_model` | 默认模型 |
| `server.auto_refresh` | 自动续期凭据 |
| `server.auto_checkin` | 自动签到 |
| `security.allow_cidrs` | 允许访问的客户端网段 |
| `security.allow_origins` | 允许的跨域来源 |
| `security.allow_hosts` | 允许的 Host |
| `providers.<id>.enabled` | 是否启用供应商 |
| `providers.<id>.accounts` | 供应商账户及凭据 |
| `providers.<id>.options` | 供应商特定选项 |

非回环地址监听需要配置工具访问密钥；远程客户端还需加入允许访问的网段。
更多配置见[英文配置说明](README.md#config-keys)，凭据处理见[鉴权说明](docs/authentication.md)。

## 供应商接入

完整供应商、来源映射、入口、协议、凭据和套餐区分见
[供应商去重清单](docs/provider-inventory.md)，账户字段见[供应商配置](docs/providers.md)。

| 类型 | 示例 |
| --- | --- |
| 免费站点及订阅账户 | Cline、TRAE、ZCode、Qoder、MiniMax Agent、Gemini Code Assist、CodeBuddy、CodeArts、EAC |
| 常规云 API | OpenAI、Anthropic、OpenRouter、NVIDIA、Groq、Mistral、Together、DeepSeek、Fireworks、xAI |
| 云平台 | Azure、Cloudflare、Google Vertex、Amazon Bedrock |
| 特殊协议与分类 | Radius、TypeSafe |
| 本地后端 | LM Studio、Ollama、llama.cpp |
| 通用 OpenAI 兼容入口 | `agg`，可填写地址和密钥，也可复用已有的 22 个预设 |

不同套餐和协议保留独立供应商 ID。例如，`minimax` 为 Agent 登录入口，
`minimax-api` 和 `minimax-chat` 为独立 API 入口；`gemini` 为 Code Assist，
`google` 为 Gemini Developer API，`google-vertex` 为 Vertex；
`amazon-bedrock` 与 `bedrock-mantle` 也分别配置。

### 登录与账户管理

支持登录的供应商可在渠道页面授权，也可以使用 CLI：

```bash
hoshimi login <provider>
hoshimi login <provider> --no-open --manual
```

第二种方式适合在其他设备上打开授权页面。设备登录会显示验证码和有效期；
需要手动回调的供应商会提示粘贴回调地址。取消登录后，迟到的回调不会保存账户。
登录成功后更新账户，失败时保留错误信息供处理。

EAC 首次登录可使用内置 relay 配置，无需先手工创建 relay 账户。
凭据刷新复用账户级锁；自动刷新与签到可在界面中关闭。
鉴权失效、限流和模型不存在会分别处理，避免把单个账户的问题误认为模型永久不可用。

### 通用兼容入口

`agg` 可连接其他 OpenAI 兼容服务：

```json
{
  "providers": {
    "agg": {
      "accounts": [
        {
          "id": "my-service",
          "base_url": "https://example.com/v1",
          "api_key": "your-provider-api-key"
        }
      ]
    }
  }
}
```

支持上游目录的服务优先读取真实目录，也可显式声明账户模型。
免费状态保留免费、付费和未知三态，不将未知价格标为免费。
LLM7 保留自身目录策略，其他预设也不会统一套用 pi-free 的默认筛选规则。

### 本地模型

`lmstudio`、`ollama`、`llamacpp` 仅在显式添加账户并启用后连接本地服务，
不会自动探测本机端口。账户可以不填 `api_key`，也可以填写服务实际要求的密钥。

LM Studio 使用已加载模型的上下文信息，Ollama 读取原生能力目录，
llama.cpp 的思考控制映射到模板参数。本地模型不会仅因本地运行而自动标为免费。

其他接入说明：[NVIDIA](docs/nvidia.md)、[pi-free](docs/pi-free.md)、
[OpenCode](docs/opencode-integration.md)、[云平台](docs/cloud-providers.md)。
参考仓库仅作为移植依据，不是运行依赖。

## 模型能力与运行行为

- 目录保留上游模型 ID，按账户隔离缓存。分页完整成功后才更新目录，失败时保留上一次结果。
- 能力以供应商和本地声明为准；models.dev 仅作为视觉、思考能力的补充，
  不替代供应商的上下文、输出上限和思考菜单。
- 只有显式选择思考强度时才转换参数；自动模式不主动添加思考设置，
  并保留调用方设置的输出 Token 上限。
- 能力允许时保留工具调用、思考签名及历史回放信息；不同协议通过统一流式事件转换。
- 可用性探测区分可用、地区限制、不可用、限流和未知，结果展示在模型页面。
- 账户鉴权失败、额度耗尽和暂时性错误按类别刷新或轮换；流式正文开始输出后不会自动重放请求。
- 用量统计记录日期、模型、Token、首字延迟和生成速度，并保留最近调用记录。

更完整的缓存、重试、探测和统计规则见[英文 README](README.md)。

## 开发

开发时从源码目录安装开发依赖；前端开发另需 Bun：

```bash
python -m pip install -e ".[dev]"
python scripts/dev.py web-install
python scripts/dev.py dev
```

开发前端默认运行在 `:5173`，代理 `:10600` 的 API。可使用 `--api-port`、
`--web-port` 调整，通过 `HOSHIMI_DATA_DIR` 指定统一的数据目录，Ctrl+C 同时结束两个进程。
源码环境下使用 `hoshimi serve` 提供界面前，需运行 `python scripts/dev.py web`；
未构建前端时只提供 API。

```bash
python scripts/dev.py lint         # Ruff
python scripts/dev.py test-py      # Python 测试
python scripts/dev.py test-web     # 前端测试与类型检查
python scripts/dev.py check        # 完整检查
python scripts/dev.py wheel        # 构建前端及 wheel / sdist
```

自动工作流统一由 `ci.yml` 触发：覆盖 `main`、`hoshimi_main` 的推送、`v*` 标签及 PR。
先运行一次共享的 `checks.yml`，再由 `build.yml` 构建前端、wheel 和 sdist，并检查安装后的发行包。
分支推送修改 `hoshimi/version.py` 或推送版本标签时，CI 会调用 `release.yml`，将同一份产物发布到 PyPI；
标签还会创建 GitHub Release。Release 不再单独监听推送，同一次推送不会重复执行检查和构建。
发行计划直接读取 Git 历史；新分支首次推送的 `before` 为全零时，使用最早推送提交的父提交进行比较，
不再依赖 GitHub 比较 API。

手动运行 Release 时仍会先执行共享检查和构建，`publish` 默认关闭，仅生成经过检查的产物。
共享检查提供 `CI passed` 汇总结果；自动发布须等待检查、构建及安装验证全部通过。
后续推送不会取消已在执行的自动发行。

前端 API 类型从服务端 OpenAPI 定义生成：

```bash
python scripts/generate_openapi.py > hoshimi/webui/openapi.json
cd hoshimi/webui && bun run typegen
```

核心逻辑位于 `hoshimi/core`，不依赖 Web 框架；`hoshimi/api`、`hoshimi/cli`、
`hoshimi/webui` 分别提供 HTTP、命令行和 Vue 界面。适配器输出统一事件，
由网关转换为 Chat Completions、Responses 或 Messages 响应。

## 许可证

本项目采用 **GNU General Public License v3.0 only（GPL-3.0-only）**。
完整条款见 [LICENSE](LICENSE)。
