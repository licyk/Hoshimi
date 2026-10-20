# 开发

[文档目录](README.zh-CN.md) | [English](development.md)

## 源码开发

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

## 检查与构建

```bash
python scripts/dev.py lint         # Ruff
python scripts/dev.py test-py      # Python 测试
python scripts/dev.py test-web     # 前端测试与类型检查
python scripts/dev.py check        # 完整检查
python scripts/dev.py wheel        # 构建前端及 wheel / sdist
```

其他任务包括 `format`、`typecheck-py`、`web-install`、`web`、`web-dev`、`dev`、
`openapi`、`typegen`、`typegen-check`，不带参数运行脚本可查看完整列表。

开发依赖包含官方 Anthropic SDK。Messages 回归使用本地模拟上游，由 SDK 消费 JSON
与流式结果，无需真实凭据；心跳和取消测试单独检查时序及完整 SSE 边界。
安装了 Claude Code 时，可按[客户端配置](anthropic-api.zh-CN.md#claude-code-配置)
对网关进行实际接入验证。

## CI 与发布

自动工作流统一由 `ci.yml` 触发：覆盖 `main`、`hoshimi_main` 的推送、`v*` 标签及 PR。
先运行一次共享的 `checks.yml`，再由 `build.yml` 构建前端、wheel 和 sdist，并检查安装后的发行包。
分支推送修改 `hoshimi/version.py` 或推送版本标签时，CI 会调用 `release.yml`，将同一份产物发布到 PyPI；
标签还会创建 GitHub Release。Release 不再单独监听推送，同一次推送不会重复执行检查和构建。
发行计划直接读取 Git 历史；新分支首次推送的 `before` 为全零时，使用最早推送提交的父提交进行比较，
不再依赖 GitHub 比较 API。

手动运行 Release 时仍会先执行共享检查和构建，`publish` 默认关闭，仅生成经过检查的产物。
共享检查提供 `CI passed` 汇总结果；自动发布须等待检查、构建及安装验证全部通过。
后续推送不会取消已在执行的自动发行。

## API 类型生成

前端 API 类型从服务端 OpenAPI 定义生成：

```bash
python scripts/generate_openapi.py > hoshimi/webui/openapi.json
cd hoshimi/webui && bun run typegen
```

## 架构

核心逻辑位于 `hoshimi/core`，不依赖 Web 框架；`hoshimi/api`、`hoshimi/cli`、
`hoshimi/webui` 分别提供 HTTP、命令行和 Vue 界面。适配器输出统一事件，
由网关转换为 Chat Completions、Responses 或 Messages 响应。

```text
hoshimi/
  core/        配置、凭据、路由、续期、能力、统一事件、translate/、providers/
  api/         FastAPI、鉴权中间件、routers/、静态界面
  cli/         Typer：serve、check、models、providers、stations、login、version、env、config
  webui/       Vue 3 + Vite：ui、views、api、stores、i18n、theme；dist/ 随发行包分发
tests/         core/、api/、cli/；分层规则确保核心不依赖 Web 框架
scripts/       开发、构建、CI 计划、OpenAPI 与供应商清单生成
```

适配器约定在 `hoshimi/core/providers/base.py`，包含 `text_delta`、`reasoning_delta`、
`reasoning_details`、`tool_call_start`、`tool_call_delta`、`usage`、`finish`、`error`
等统一事件，由协议输出层生成 Chat、Responses 或 Messages。
`codex-api`（服务与安全）、`dsh-our-free-model`（站点协议）、`Hanaikada`（分层与界面骨架）
是外部参考克隆，已 git-ignore，不是运行依赖。

## 文档维护

手写主题按英文与 `.zh-CN.md` 对应维护，更新公共行为时同步双方。
配置以 `hoshimi/core/config.py` 和 `paths.py` 为准，路由以 `hoshimi/api/routers/` 为准，
供应商注册与账户字段以 `factory.py` 和 `provider_forms.py` 为准。
来源清单从 `provider_sources.py` 生成：

```bash
python scripts/generate_provider_inventory.py
```

生成表仅保留一份，页面目录提供双语说明。维护后检查相对路径、标题锚点和 JSON 示例，
并运行 `git diff --check`；根 README 的完整 GitHub 链接也用于包介绍展示。
