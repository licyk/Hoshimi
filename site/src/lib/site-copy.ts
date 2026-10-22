import { useI18n } from "fumadocs-ui/contexts/i18n";

import { DEFAULT_LOCALE, isLocale, type Locale } from "@/lib/i18n";

const zhCN = {
  pageRailLabel: "页面区块导航",
  theme: {
    hint: "点击切换主题",
    modes: { system: "主题：跟随系统", light: "主题：浅色", dark: "主题：深色" },
  },
  comments: "评论",
  home: {
    sections: ["首页", "功能", "使用流程", "特点", "安装"],
    hero: {
      eyebrow: "兼容 OpenAI 与 Anthropic 的模型网关",
      title: "一个 API，",
      titleAccent: "接入所有模型站点",
      description:
        "Hoshimi 把 Cline、TRAE、ZCode、Qoder、MiniMax、Gemini、CodeBuddy、Kilo 等免费模型站点，以及云服务与本地模型，汇聚到同一个 API 和网页界面：Chat Completions、Responses 与 Anthropic Messages 三种协议任选，账号登录、续期与轮换自动完成，还带一个能动手的 Playground。",
      start: "开始使用",
      learn: "了解",
      platformsLabel: "支持的平台",
    },
    features: {
      title: "从一个账号到一整套工作流",
      description: "网页界面的每个页面都有对应的教程；同样的事情也都能通过命令行和 HTTP API 完成。",
      open: "查看教程",
      items: [
        {
          title: "一个接口",
          description:
            "OpenAI Chat Completions、Responses 与 Anthropic Messages 共用一个地址，不同站点的协议差异由 Hoshimi 转换；Claude Code、Codex、OpenCode 等客户端直接接入。",
          href: "/docs/clients",
        },
        {
          title: "渠道",
          description: "API 密钥、浏览器登录与设备码登录统一管理；令牌到期前自动续期，余额、签到与过期时间一目了然。",
          href: "/docs/guide-channels",
        },
        {
          title: "模型",
          description: "汇总每个渠道的模型目录，标出能力、推理档位与免费标记，并用探测检查哪些模型此刻可用。",
          href: "/docs/guide-models",
        },
        {
          title: "Playground 与智能体",
          description: "保存的对话、分支与版本、图片与文件附件；打开工具后，模型可以读写文件、运行命令、搜索网页，每一步都由你批准。",
          href: "/docs/guide-agent",
        },
        {
          title: "用量",
          description: "进行中的调用实时可见，按小时查看任意一天的令牌、缓存、首字延迟与生成速度，全部保存在本地 SQLite 中。",
          href: "/docs/guide-usage",
        },
      ],
    },
    workflow: {
      title: "从安装到第一次调用",
      description: "四步走完整个流程，每一步在网页界面和命令行中都有对应做法。",
      steps: [
        {
          title: "安装 Hoshimi",
          description: "需要 Python 3.10 或更新版本：pip install hoshimi。网页界面已打包在内，无需构建前端。",
        },
        {
          title: "启动服务",
          description: "运行 hoshimi serve，打开 http://127.0.0.1:10600。OpenCode Zen 与 Kilo 的匿名入口默认开启，无需配置即可试用。",
        },
        {
          title: "添加渠道",
          description: "在 渠道 页面填入密钥或登录站点账号；也可以用 hoshimi login 在命令行中完成。",
        },
        {
          title: "接入客户端",
          description: "把客户端的 Base URL 指向 http://127.0.0.1:10600/v1，用 Hoshimi 列出的模型 ID 发起请求。",
        },
      ],
    },
    highlights: {
      title: "站点各不相同，用法始终一致",
      description: "账号、协议、配额与故障的差异交给 Hoshimi 处理，你只需要一个地址和一个密钥。",
      items: [
        {
          title: "三种协议，一个地址",
          description: "任一客户端协议都能调用任一渠道的模型：流式输出、推理内容、工具调用与图片在协议之间转换。",
        },
        {
          title: "开箱即用",
          description: "安装后无需任何配置文件就能对话：匿名入口默认开启，之后再按需添加自己的账号。",
        },
        {
          title: "账号自动维护",
          description: "令牌自动续期，失败时在账号间轮换重试，记住不可用的模型，每日签到领取额度。",
        },
        {
          title: "默认安全",
          description: "默认只监听本机；工具访问密钥与模型 API 密钥相互独立，并可按网段、主机名与来源限制访问。",
        },
        {
          title: "能动手的 Playground",
          description: "内置读取文件、编辑补丁、命令行、后台命令、网页搜索与抓取六种工具，三种审批模式，回复进行中也能补充要求。",
        },
      ],
    },
    install: {
      title: "按文档完成安装",
      description:
        "Hoshimi 从 PyPI 安装，pip、uv 或 pipx 均可，也可以从源代码运行。安装文档说明系统要求、可选的云服务依赖、升级方式与以服务方式运行。",
      link: "查看安装文档",
      href: "/docs/installation",
    },
    footer: {
      label: "页脚导航",
      docs: "文档",
      quickStart: "快速开始",
      faq: "常见问题",
      repository: "源代码",
      socialLabel: "社交链接",
      social: { blog: "博客", github: "GitHub", bilibili: "哔哩哔哩" },
    },
    preview: {
      label: "Hoshimi Playground 界面示意",
      title: "Playground",
      question: "运行测试，修复失败的用例",
      tools: [
        { verb: "运行", detail: "pytest -q", time: "2.4 s" },
        { verb: "编辑", detail: "src/parser.py", time: "" },
      ],
      answer: "两个失败的用例都来自解析器对空行的处理，已修复并重新运行：全部 128 个测试通过。",
      placeholder: "补充说明，模型会在下一步读到…",
      approval: "询问",
    },
  },
} as const;

type TranslationShape<T> = T extends string
  ? string
  : T extends readonly unknown[]
    ? { readonly [K in keyof T]: TranslationShape<T[K]> }
    : T extends object
      ? { readonly [K in keyof T]: TranslationShape<T[K]> }
      : T;

export type SiteCopy = TranslationShape<typeof zhCN>;

const en = {
  pageRailLabel: "Page sections",
  theme: {
    hint: "Click to switch theme",
    modes: { system: "Theme: follow system", light: "Theme: light", dark: "Theme: dark" },
  },
  comments: "Comments",
  home: {
    sections: ["Home", "Features", "Workflow", "Highlights", "Install"],
    hero: {
      eyebrow: "An OpenAI- and Anthropic-compatible model gateway",
      title: "One API, ",
      titleAccent: "every model station",
      description:
        "Hoshimi brings free model stations such as Cline, TRAE, ZCode, Qoder, MiniMax, Gemini, CodeBuddy and Kilo, along with cloud services and local models, behind one API and one web UI: Chat Completions, Responses or Anthropic Messages as you like, accounts signed in, renewed and rotated for you, and a Playground that can get work done.",
      start: "Get started",
      learn: "Explore ",
      platformsLabel: "Supported platforms",
    },
    features: {
      title: "From one account to a whole workflow",
      description:
        "Every page of the web UI has its own guide, and everything it does can also be done from the command line and the HTTP API.",
      open: "Read the guide",
      items: [
        {
          title: "One API",
          description:
            "OpenAI Chat Completions, Responses and Anthropic Messages at one address; Hoshimi translates between the stations' protocols, and clients such as Claude Code, Codex and OpenCode connect directly.",
          href: "/docs/clients",
        },
        {
          title: "Channels",
          description:
            "API keys, browser sign-in and device codes in one place; tokens renewed before they expire, with balances, check-ins and expiry dates at a glance.",
          href: "/docs/guide-channels",
        },
        {
          title: "Models",
          description:
            "Every channel's catalogue in one list, with capabilities, reasoning levels and free markers, and probes that check which models answer right now.",
          href: "/docs/guide-models",
        },
        {
          title: "Playground and agent",
          description:
            "Kept conversations with branches and versions, images and files; turn tools on and the model reads and edits files, runs commands and searches the web, each step with your approval.",
          href: "/docs/guide-agent",
        },
        {
          title: "Usage",
          description:
            "Calls in flight as they happen; tokens, cache, first-token latency and speed hour by hour for any day, all kept in a local SQLite database.",
          href: "/docs/guide-usage",
        },
      ],
    },
    workflow: {
      title: "From installation to your first call",
      description: "Four steps through the whole workflow, each with a web UI and a command-line way to do it.",
      steps: [
        {
          title: "Install Hoshimi",
          description: "Python 3.10 or newer: pip install hoshimi. The web UI comes built in; there is no frontend to build.",
        },
        {
          title: "Start the server",
          description:
            "Run hoshimi serve and open http://127.0.0.1:10600. The OpenCode Zen and Kilo anonymous entry points are on by default, so you can try it with no configuration.",
        },
        {
          title: "Add channels",
          description: "Enter keys or sign in to station accounts on the Channels page, or with hoshimi login from the command line.",
        },
        {
          title: "Connect a client",
          description: "Point the client's base URL at http://127.0.0.1:10600/v1 and use a model id Hoshimi lists.",
        },
      ],
    },
    highlights: {
      title: "Different stations, one way of working",
      description: "Hoshimi takes care of accounts, protocols, quotas and failures; you need one address and one key.",
      items: [
        {
          title: "Three protocols, one address",
          description:
            "Any client protocol reaches any channel's models: streaming, reasoning, tool calls and images are translated between them.",
        },
        {
          title: "Works out of the box",
          description:
            "Chat right after installing, with no configuration file: the anonymous entry points are on by default, and your own accounts come when you need them.",
        },
        {
          title: "Accounts that look after themselves",
          description:
            "Tokens renewed automatically, retries rotated across accounts, unavailable models remembered, and daily check-ins claimed.",
        },
        {
          title: "Safe by default",
          description:
            "Listens on this machine only; the tool access key and the model API key are separate, and access can be limited by network, host name and origin.",
        },
        {
          title: "A Playground that does things",
          description:
            "Six built-in tools (read, apply_patch, shell, background commands, web search and fetch), three approval modes, and a reply you can steer while it runs.",
        },
      ],
    },
    install: {
      title: "Install with the guide",
      description:
        "Install Hoshimi from PyPI with pip, uv or pipx, or run it from source. The installation guide covers the requirements, the optional cloud dependencies, upgrading and running it as a service.",
      link: "Read the installation guide",
      href: "/docs/installation",
    },
    footer: {
      label: "Footer navigation",
      docs: "Docs",
      quickStart: "Quick start",
      faq: "FAQ",
      repository: "Source code",
      socialLabel: "Social links",
      social: { blog: "Blog", github: "GitHub", bilibili: "Bilibili" },
    },
    preview: {
      label: "Sketch of Hoshimi's Playground",
      title: "Playground",
      question: "Run the tests and fix what fails",
      tools: [
        { verb: "Run", detail: "pytest -q", time: "2.4 s" },
        { verb: "Edit", detail: "src/parser.py", time: "" },
      ],
      answer: "Both failures came from how the parser handles blank lines. Fixed and run again: all 128 tests pass.",
      placeholder: "Add to what it’s doing…",
      approval: "Ask",
    },
  },
} as const satisfies SiteCopy;

const SITE_COPY: Record<Locale, SiteCopy> = { "zh-CN": zhCN, en };

export function getSiteCopy(locale: Locale): SiteCopy {
  return SITE_COPY[locale];
}

export function useSiteLocale(): Locale {
  const { locale } = useI18n();
  return isLocale(locale) ? locale : DEFAULT_LOCALE;
}

export function useSiteCopy(): SiteCopy {
  return getSiteCopy(useSiteLocale());
}
