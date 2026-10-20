# 鉴权与账户

[文档目录](README.zh-CN.md) | [English](authentication.md)

## 网关密钥和上游凭据

| 凭据 | 用途 | 传入方式 |
| --- | --- | --- |
| `server.access_token` | 管理 `/api/v1/*`、`/docs`、`/openapi.json` | `Authorization: Bearer <tool-access-key>` |
| `server.model_api_key` | 外部 `/v1/*` 推理 | `Authorization: Bearer <model-api-key>` 或 `x-api-key` |
| 供应商账户密钥或令牌 | Hoshimi 访问上游 | 保存到 `providers.<id>.accounts` |

两种网关密钥独立生效，留空仅关闭对应接口的鉴权。显式 Authorization 优先于
`x-api-key`，后者不能打开管理接口。健康检查与界面静态页面公开，但仍受网络策略约束。

在设置中配置密钥。浏览器需要时提示工具访问密钥，验证成功后保存到 localStorage，
登录失败不会替换已有凭据。修改或清空密钥后，当前浏览器在服务端确认后更新保存值，
其他浏览器需要重新登录。Playground 使用管理接口，不需要单独输入模型 API 密钥；
设置响应只返回是否已配置的标记，不返回密钥原文。

## 登录授权

```bash
hoshimi login
hoshimi login <provider>
hoshimi login <provider> --no-open --manual
```

渠道页面提供相同的浏览器与设备登录流程。登录成功后保存到账户配置；同一供应商身份
再次登录会更新原账户。设备流程显示验证码和有效期；支持手动回调的供应商可以粘贴
授权码或回调 URL。`--manual` 不代表所有回环登录都可跨设备完成。
取消登录后，迟到的回调不会保存账户。

| 供应商 | 授权方式 |
| --- | --- |
| `cline` | workos.com 设备码，随后注册 |
| `codex` | OpenAI 授权，PKCE 回调固定回环端口 1455 |
| `minimax` | account.minimax.cn 设备码与 PKCE |
| `buddy` / `workbuddy` | 外部登录页，轮询令牌 |
| `zcode` | zcode.z.ai 服务端中转，无本地回调 |
| `qoder` / `qodercn` | qoder.com 设备链接，在开放 API 主机轮询 |
| `gemini` | Google 授权，回调回环端口 |
| `trae` | 控制台登录，回调端口 18080 |
| `lobsterai` | 门户登录，回调回环端口 |
| `loomy` | 本地页面展示微信二维码，首次账户需要绑定手机 |
| `codearts` | 华为云门户，PKCE 与令牌交换 DPoP 证明 |
| `raccoon` | 微信二维码，网关轮询 |
| `eac` | GitHub 授权，使用本安装的 `x-ofm-user` 令牌 |

回环登录只监听 `127.0.0.1`。通常使用临时端口；TRAE 优先 18080，CodeArts
要求高于 10000，Codex 使用注册回调要求的固定 1455。Codex 账户通过原生 Responses
适配器推理。其他授权入口还包括 Anthropic、GitHub Copilot、Kilo、Kimi Coding、Meta、
OpenAI、OpenRouter、Radius 和 xAI，以 `hoshimi login` 列表为准。
管理 API 提供 `/login/providers`、`/login/start`、`/login/poll`、`/login/input` 与
`/login/cancel`，均位于 `/api/v1` 下。

## 存储和续期

凭据位于数据目录的 `config.json`，用 `hoshimi config path` 查看。文件采用原子替换
和尽可能严格的权限；解析错误时先尽可能备份并拒绝启动，不会静默覆盖。不要共享已填
凭据的配置文件。

启用自动续期后，每 30 分钟检查一次未来一小时内过期的账户。刷新令牌被拒绝时标记
`needs_login`，需要重新登录。`GET /api/v1/refresh` 查看状态，`POST` 手动续期。
续期操作复用账户锁。鉴权、额度和模型错误分别处理，详见
[模型与运行行为](model-behavior.zh-CN.md)。

## EAC

EAC 没有匿名模式，配置账户后才建立服务通道；供应商元数据仍会显示需要授权。
首次运行 `hoshimi login eac` 或在渠道页面登录，可以使用内置 relay 完成 GitHub 授权，
创建带 `relay: "bundled"` 的 `eac-default`。共享签名材料不写入配置。

自定义 relay 支持两种模式：

- direct：`base_url` 与 `access_token`，通过 Bearer 访问，不使用 GitHub OAuth。
- worker：`base_url` 与 `signing_secret`，使用毫秒时间戳及
  `HMAC-SHA256(timestamp\nMETHOD\npath\nsha256(body))`，发送
  `x-ofm-timestamp`、`x-ofm-signature` 和 GitHub 授权 `x-ofm-user`。

签名覆盖实际发送的字节，签名后不能重新编码请求体。worker 登录保留已有 relay 地址和
签名密钥。账户省略 `user_token` 时，每次请求读取最新 `eac-user.json`；显式设为
`null` 或空字符串会关闭该账户的回退读取。缺少授权的 worker 返回
`401 AuthorizationRequired`。

## 网络访问

非回环监听需要工具访问密钥，远程客户端还需加入 `security.allow_cidrs`。
通过模型 API 密钥独立控制推理访问。跨域来源白名单要求工具访问密钥，Host 和浏览器
Origin 检查仍然生效；持有密钥不能绕过网络策略。
局域网配置示例见[配置](configuration.zh-CN.md)。
