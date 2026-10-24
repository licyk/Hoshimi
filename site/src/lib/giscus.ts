import { languageIndependentPath, type Locale, routerBasePath } from "@/lib/i18n";

export const GISCUS_ORIGIN = "https://giscus.app";

/**
 * Fixed giscus attributes for the documentation comments (as rvc-next's site).
 *
 * Theme and language are left out: the site has its own theme toggle and
 * language switch, so both follow the page rather than the browser. The term
 * is set per page by `giscusTerm` rather than giscus's own `pathname` mapping.
 */
export const GISCUS_ATTRIBUTES = {
  "data-repo": "licyk/Hoshimi",
  "data-repo-id": "R_kgDOVP9Zpg",
  "data-category": "Comment",
  "data-category-id": "DIC_kwDOVP9Zps4DJWTI",
  "data-mapping": "specific",
  "data-strict": "1",
  "data-reactions-enabled": "1",
  "data-emit-metadata": "0",
  "data-input-position": "top",
  "data-loading": "lazy",
} as const;

/**
 * The discussion term for a page path: one discussion per page, wherever it is served.
 *
 * giscus's `pathname` mapping takes `location.pathname` verbatim, which would
 * split a page's discussion: GitHub Pages serves the site under `/Hoshimi/`
 * while Netlify serves it at the root, a host may answer `/docs/page/` where
 * in-site navigation arrives at `/docs/page`, and a language prefix
 * (`/en/docs/page`) names the same page. This drops the base path, the language
 * and the slashes around it, keeping giscus's form (no leading slash, `index`
 * for the root).
 */
export function giscusTerm(pathname: string, baseUrl: string = import.meta.env.BASE_URL): string {
  const base = routerBasePath(baseUrl);
  let path = pathname;
  if (base !== "/" && (path === base || path.startsWith(`${base}/`))) path = path.slice(base.length) || "/";
  path = languageIndependentPath(`/${path.replace(/^\/+/, "")}`).replace(/^\/+|\/+$/g, "");
  return path || "index";
}

export type GiscusTheme = "light" | "dark";

/** The theme `next-themes` resolved, or light while it is still unknown. */
export function giscusTheme(resolvedTheme: string | undefined): GiscusTheme {
  return resolvedTheme === "dark" ? "dark" : "light";
}

const GISCUS_LANGUAGES: Record<Locale, string> = {
  "zh-CN": "zh-CN",
  en: "en",
};

export function giscusLanguage(locale: Locale): string {
  return GISCUS_LANGUAGES[locale];
}
