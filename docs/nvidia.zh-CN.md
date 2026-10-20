# NVIDIA NIM

[文档目录](README.zh-CN.md) | [English](nvidia.md)

## 配置账户

在渠道页面添加 NVIDIA 账户，或写入数据目录配置，用 `hoshimi config path` 查看位置：

```json
{
  "providers": {
    "nvidia": {
      "accounts": [{"id": "nvidia-1", "api_key": "your-nvidia-api-key"}]
    }
  }
}
```

文件修改后重启，界面添加账户即时生效。从 `/v1/models` 获取 ID，使用
`nvidia/<model-id>` 显式路由。默认上游为 `https://integrate.api.nvidia.com/v1`。
上游密钥与网关的模型、工具密钥分别配置。

## 目录与兼容行为

适配器实时发现模型，排除已识别的嵌入、重排、奖励评分、检测和文档解析任务。
列出模型不代表额度可用或推理成功，可显式执行可用性探测。
能力与账户选项遵循[模型规则](model-behavior.zh-CN.md)。

Chat 请求移除不支持的 `store`、`reasoning_effort`、`reasoning`、
`prompt_cache_retention`。未提供 `max_tokens` 时，将 `max_completion_tokens`
转为该字段；developer 消息转为 system。工具定义会清理不支持的 strict 和 schema
字段，MiniMax 工具流通过原生工具适配器归一化。这些转换不等于完整 OpenAI 功能兼容。

适配器发送 `NVCF-POLL-SECONDS: 3600`。实际密钥权限、额度和模型访问决定请求结果。
客户端设置见 [API](api.zh-CN.md)，通用账户说明见[供应商](providers.zh-CN.md)。
