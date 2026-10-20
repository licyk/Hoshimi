# pi-free 兼容说明

[文档目录](README.zh-CN.md) | [English](pi-free.md)

## 范围与清单

pi-free 是站点行为和目录规则的移植参考，不是运行依赖；pi、free-claude-code 也是
参考来源。[供应商清单](provider-inventory.md) 记录来源 ID、端点、协议、鉴权、套餐和
覆盖。快照描述来源基线，不表示上游最新状态或已验证所有实时接口。

原生供应商 ID 与 22 个 `agg` 预设保留。凭据由 Hoshimi 账户提供，`hoshimi stations`
展示的环境变量仅描述上游惯例，不自动读取为本应用密钥。
账户和协议区别见[供应商](providers.zh-CN.md)。

## 目录筛选

`agg` 按端点地址匹配站点规则，不仅按预设名，避免将其他站点的 schema 套在自定义 relay。
十个预设使用移植的聊天任务筛选，元数据未知时保留，避免上游字段变化导致整个目录消失。

LLM7 使用 Hoshimi 的 `model_type` 规则，因为仅检查文本输出会误纳入语音转写和生成
描述的图片模型。不添加 pi-free 静态 default/fast/pro 选择器：它们不来自实时目录，
历史探测也未确认匿名可用。这是历史设计依据，不是当前端点健康报告。

## 免费状态

免费标记保留 true、false、null，未知不等于付费。来源包括 B.AI、TokenRouter 的
`:free` ID，GMI 的 `is_free`，XKiro 的 `access_tier`，AnyAPI、OrcaRouter、OpenGateway
的价格行，以及 Agnes、CommandCode 的整理清单。

不复制 pi-free 在发现部分定价后将缺失价格视为付费的策略。Venice 的零价格也不作为
可免费访问的证据，因为无余额账户仍可能被拒绝。免费标记不承诺所有密钥有额度或权限。

## 能力和模型选择

保留 CommandCode 站点的视觉与思考能力列表，账户声明更优先；不引入无关的付费价格表
或默认输出上限。models.dev 仅补充未声明的视觉和思考，限制与菜单必须对应实际端点。

不导入 Artificial Analysis 快照和模糊名称匹配排名。Hoshimi 路由指定模型并记忆不可用
ID，不静默替换为基准排名更高的模型。

## 运行差异

模型失败记忆十分钟过期，窗口内三次升级禁用。轮换对象是账户，不是替换模型 ID。
被动配额保留相同的头部顺序，但数值严格解析：`10 requests` 不会解析为 10，
而是计为解析漂移。完整当前规则见[模型与运行行为](model-behavior.zh-CN.md)。
