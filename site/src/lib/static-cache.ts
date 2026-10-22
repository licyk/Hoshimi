/**
 * TanStack Start's static server functions (the docs pages' data) are prerendered into
 * `__tsr/staticServerFnCache/` beside the pages, but the client fetches them from the domain's
 * root (`/__tsr/…`). Under a base path — the GitHub Pages copy at `/Hoshimi/` — that is another
 * site, and every page opened by a link comes up empty. This sends those requests under the base.
 */
export function staticCacheUrl(url: string, base: string): string {
  return url.startsWith("/__tsr/") && base !== "/" ? `${base.replace(/\/+$/, "")}${url}` : url;
}

export function routeStaticCacheUnderBase(base: string = import.meta.env.BASE_URL): void {
  if (typeof window === "undefined" || base === "/" || "hoshimiStaticCache" in window.fetch) return;
  const original = window.fetch.bind(window);
  const routed: typeof window.fetch = (input, init) =>
    original(typeof input === "string" ? staticCacheUrl(input, base) : input, init);
  window.fetch = Object.assign(routed, { hoshimiStaticCache: true });
}
