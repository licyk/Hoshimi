# Hoshimi

[English](https://github.com/licyk/Hoshimi/blob/main/README.md) | [简体中文](https://github.com/licyk/Hoshimi/blob/main/README.zh-CN.md)

**[官网](https://hoshimi.netlify.app)** · **[文档](https://hoshimi.netlify.app/docs)**

探索星辰，连接不同的模型。

Hoshimi 将免费模型站点、云服务和本地模型接入同一个网关，提供 OpenAI 与 Anthropic
兼容 API，以及用于管理账户和模型的 Web UI。

- **统一接口**：Chat Completions、Responses 和 Anthropic Messages。
- **账户管理**：API Key、浏览器或设备授权、凭据续期与账户轮换。
- **模型目录**：能力、思考选项与可用性探测。
- **实时用量**：进行中的调用、Token 与缓存明细、首字延迟和生成速度，可按小时查看任意一天，数据保存在本地 SQLite 数据库中。
- **多语言界面**：管理渠道、模型、聊天、用量与设置。

## 快速开始

需要 **Python 3.10 或更高版本**：

```bash
python -m pip install -U hoshimi
hoshimi serve
```

打开 **http://127.0.0.1:10600**。发行包已包含构建好的 Web UI，无需自行构建前端。
端口占用时，请使用终端实际打印的 URL。

默认启用 OpenCode Zen 与 Kilo 匿名入口，无需配置文件。
实际可用性取决于上游模型、额度和服务状态。
通过**渠道**页面添加其他供应商账户，在**设置**中配置网关。

`python -m hoshimi` 与 `hoshimi` 等价。查看统一配置文件位置：

```bash
hoshimi config path
```

可选云依赖、CLI 参数、源码安装见
[安装指南](https://hoshimi.netlify.app/docs/installation)，
配置路径与旧版迁移见[配置说明](https://hoshimi.netlify.app/docs/config)。

## 首次调用

查询目录，将返回的一个模型 ID 填入 `<model-id>`：

```bash
curl http://127.0.0.1:10600/v1/models

curl http://127.0.0.1:10600/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"<model-id>","messages":[{"role":"user","content":"你好"}]}'
```

已配置模型 API 密钥时，为两个请求添加 `Authorization: Bearer <model-api-key>`。
Chat Completions 默认流式输出，传入 `"stream": false` 可返回 JSON。

OpenAI 兼容客户端的 Base URL 通常设为 `http://127.0.0.1:10600/v1`；
Anthropic 兼容 Agent 使用 `http://127.0.0.1:10600`，从同一目录选择模型。
详细说明见 [API 接入](https://hoshimi.netlify.app/docs/clients)和
[Anthropic Agent](https://hoshimi.netlify.app/docs/clients-anthropic)。

## 访问密钥

在设置中分别配置两种独立密钥：

- **工具访问密钥**：保护管理接口、设置和 API 文档。
- **模型 API 密钥**：保护外部 `/v1/*` 推理请求。

供应商自身的凭据在渠道账户中单独配置。网关密钥留空仅关闭对应接口的鉴权。
局域网访问需配置工具密钥和允许的客户端网段，见
[鉴权说明](https://hoshimi.netlify.app/docs/config-security)。

## 文档

[完整文档目录](https://hoshimi.netlify.app/docs)

| 用途 | 指南 |
| --- | --- |
| 安装、升级和使用 Web UI | [安装](https://hoshimi.netlify.app/docs/installation) |
| 配置字段、路径和旧版迁移 | [配置](https://hoshimi.netlify.app/docs/config) |
| 密钥、登录与 EAC | [鉴权](https://hoshimi.netlify.app/docs/config-security) |
| 接入客户端和 Agent | [API](https://hoshimi.netlify.app/docs/clients) · [Anthropic](https://hoshimi.netlify.app/docs/clients-anthropic) |
| 添加云服务与本地账户 | [供应商](https://hoshimi.netlify.app/docs/providers) · [云平台](https://hoshimi.netlify.app/docs/providers-cloud) · [NVIDIA](https://hoshimi.netlify.app/docs/providers-nvidia) |
| 了解能力、重试、配额和统计 | [模型与运行行为](https://hoshimi.netlify.app/docs/advanced-models) |
| 开发、构建和发布 | [开发](https://hoshimi.netlify.app/docs/development) |
| 查看移植覆盖和协议差异 | [供应商清单](https://hoshimi.netlify.app/docs/providers-inventory) · [OpenCode](https://hoshimi.netlify.app/docs/clients-opencode) · [pi-free](https://hoshimi.netlify.app/docs/advanced-pi-free) |

完整端点定义见运行中服务的 `/docs` 和 `/openapi.json`，已配置鉴权时使用工具访问密钥。

## 许可证

本项目采用 **GNU General Public License v3.0 only（GPL-3.0-only）**。
完整条款见 [LICENSE](https://github.com/licyk/Hoshimi/blob/main/LICENSE)。
