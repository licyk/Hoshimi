/**
 * Where the site lives and the services it talks to.
 *
 * Netlify serves it at the root of `hoshimi.netlify.app`; GitHub Pages keeps a copy under
 * `/Hoshimi/` (`VITE_BASE_PATH`). Every page names the Netlify address as its canonical one, so
 * search engines index one copy. A fork sets `VITE_SITE_URL` and `VITE_UMAMI_WEBSITE_ID` (empty
 * turns analytics off) for its own.
 */
export const SITE_URL = (import.meta.env.VITE_SITE_URL || "https://hoshimi.netlify.app").replace(/\/+$/, "");

/** The canonical address of a path on the site (`/`, `/docs/quick-start`). */
export function canonicalUrl(path: string): string {
  const clean = `/${path}`.replace(/\/{2,}/g, "/");
  return clean === "/" ? `${SITE_URL}/` : `${SITE_URL}${clean.replace(/\/$/, "")}`;
}

/** Umami analytics: production builds only, and counted on the site's own domains alone. */
export const UMAMI = {
  src: "https://licyk-umami.netlify.app/script.js",
  websiteId: import.meta.env.VITE_UMAMI_WEBSITE_ID ?? "84981b75-f431-403b-a4b8-0740477715d7",
  domains: "hoshimi.netlify.app,licyk.github.io",
};

export function analyticsScripts(production: boolean = import.meta.env.PROD) {
  if (!production || !UMAMI.websiteId) return [];
  return [{ src: UMAMI.src, defer: true, "data-website-id": UMAMI.websiteId, "data-domains": UMAMI.domains }];
}

/**
 * Giscus comments, one GitHub Discussions thread per documentation page.
 *
 * The thread is found by the page's own path (`/docs/<slug>`), not the address bar's: the
 * GitHub Pages copy lives under `/Hoshimi/`, and both languages share a URL, so `pathname`
 * mapping would split a page's comments in two.
 */
export const GISCUS = {
  repo: "licyk/Hoshimi",
  repoId: "R_kgDOVP9Zpg",
  category: "Comment",
  categoryId: "DIC_kwDOVP9Zps4DJWTI",
} as const;

export function commentsTerm(slugs: readonly string[]): string {
  return slugs.length ? `/docs/${slugs.join("/")}` : "/docs";
}
