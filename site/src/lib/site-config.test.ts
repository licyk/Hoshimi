import { describe, expect, it } from "vitest";

import { analyticsScripts, canonicalUrl, docsPath, UMAMI } from "@/lib/site-config";

describe("canonical addresses", () => {
  it("name the Netlify site, whichever host serves the copy", () => {
    expect(canonicalUrl("/")).toBe("https://hoshimi-dev.netlify.app/");
    expect(canonicalUrl("/docs/quick-start")).toBe("https://hoshimi-dev.netlify.app/docs/quick-start");
    expect(canonicalUrl("docs/quick-start/")).toBe("https://hoshimi-dev.netlify.app/docs/quick-start");
  });
});

describe("documentation paths", () => {
  it("name a page by its own path, the same in both languages", () => {
    expect(docsPath([])).toBe("/docs");
    expect(docsPath(["guide-agent"])).toBe("/docs/guide-agent");
  });
});

describe("analytics", () => {
  it("load in production builds only, counted on the site's own domains", () => {
    expect(analyticsScripts(false)).toEqual([]);
    expect(analyticsScripts(true)).toEqual([
      { src: UMAMI.src, defer: true, "data-website-id": UMAMI.websiteId, "data-domains": "hoshimi-dev.netlify.app,licyk.github.io" },
    ]);
  });
});
