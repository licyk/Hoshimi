# Hoshimi 文档

[项目首页](../README.zh-CN.md) | [English](README.md)

首次使用按安装、配置、客户端接入顺序阅读；仅使用匿名入口时可直接启动。

| 按用途阅读 |
| --- |
| [安装升级、CLI 与 Web UI](installation.zh-CN.md) |
| [配置、路径与旧版迁移](configuration.zh-CN.md) |
| [密钥、登录、续期与 EAC](authentication.zh-CN.md) |
| [API、客户端与模型路由](api.zh-CN.md) |
| [Anthropic 与 Agent 接入](anthropic-api.zh-CN.md) |
| [供应商账户、agg 与本地模型](providers.zh-CN.md) |
| [Azure、Cloudflare、Vertex、Bedrock 与特殊协议](cloud-providers.zh-CN.md) |
| [NVIDIA NIM](nvidia.zh-CN.md) |
| [能力、思考、探测、重试、配额与统计](model-behavior.zh-CN.md) |
| [开发、架构、检查与发布](development.zh-CN.md) |
| [OpenCode 协议与验证范围](opencode-integration.zh-CN.md) |
| [pi-free 移植与兼容差异](pi-free.zh-CN.md) |
| [供应商来源与去重清单（生成文档，共享英文表）](provider-inventory.md) |

中英文手写文档按主题对应维护，使用页面顶部链接切换语言。
运行时 `/docs` 和 `/openapi.json` 提供完整 API 定义，与仓库用户指南分别维护。

## 供应商清单

`provider-inventory.md` 由 `hoshimi/core/provider_sources.py` 生成，仅维护一份：

```bash
python scripts/generate_provider_inventory.py
```

来源快照和覆盖标记不代表当前实时可用性。原生注册、套餐和鉴权区别见供应商与专项文档。
参考仓库不是运行依赖。
