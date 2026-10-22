import { describe, expect, it } from "vitest";

import { analyticsScripts, canonicalUrl, commentsTerm, UMAMI } from "@/lib/site-config";

describe("canonical addresses", () => {
  it("name the Netlify site, whichever host serves the copy", () => {
    expect(canonicalUrl("/")).toBe("https://hoshimi.netlify.app/");
    expect(canonicalUrl("/docs/quick-start")).toBe("https://hoshimi.netlify.app/docs/quick-start");
    expect(canonicalUrl("docs/quick-start/")).toBe("https://hoshimi.netlify.app/docs/quick-start");
  });
});

describe("comments", () => {
  it("find a page's thread by its own path, the same on both hosts and in both languages", () => {
    expect(commentsTerm([])).toBe("/docs");
    expect(commentsTerm(["guide-agent"])).toBe("/docs/guide-agent");
  });
});

describe("analytics", () => {
  it("load in production builds only, counted on the site's own domains", () => {
    expect(analyticsScripts(false)).toEqual([]);
    expect(analyticsScripts(true)).toEqual([
      { src: UMAMI.src, defer: true, "data-website-id": UMAMI.websiteId, "data-domains": "hoshimi.netlify.app,licyk.github.io" },
    ]);
  });
});
