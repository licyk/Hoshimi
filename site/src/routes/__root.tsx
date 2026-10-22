import { createRootRoute, HeadContent, Outlet, Scripts } from "@tanstack/react-router";

import favicon from "@/assets/favicon.svg?url&no-inline";
import { SiteProvider } from "@/components/site-provider";
import { DEFAULT_LOCALE } from "@/lib/i18n";
import { analyticsScripts } from "@/lib/site-config";
import appCss from "@/styles/app.css?url";

export const Route = createRootRoute({
  head: () => ({
    meta: [
      { charSet: "utf-8" },
      { name: "viewport", content: "width=device-width, initial-scale=1" },
      {
        title: "Hoshimi",
      },
      {
        name: "description",
        content: "Hoshimi：兼容 OpenAI 与 Anthropic 的模型网关，把免费模型站点、云服务与本地模型汇聚到同一个 API 与网页界面。",
      },
    ],
    links: [
      { rel: "stylesheet", href: appCss },
      {
        rel: "icon",
        type: "image/svg+xml",
        href: favicon,
      },
    ],
    // Umami, in production builds only (see site-config).
    scripts: analyticsScripts(),
  }),
  component: RootComponent,
});

function RootComponent() {
  return (
    <html lang={DEFAULT_LOCALE} suppressHydrationWarning>
      <head>
        <HeadContent />
      </head>
      <body className="flex min-h-screen flex-col">
        <SiteProvider>
          <Outlet />
        </SiteProvider>
        <Scripts />
      </body>
    </html>
  );
}
