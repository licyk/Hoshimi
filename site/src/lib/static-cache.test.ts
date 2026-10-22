import { describe, expect, it } from "vitest";

import { staticCacheUrl } from "@/lib/static-cache";

describe("the static server-function cache", () => {
  it("is fetched under the base path when the site has one", () => {
    expect(staticCacheUrl("/__tsr/staticServerFnCache/abc.json", "/Hoshimi/")).toBe("/Hoshimi/__tsr/staticServerFnCache/abc.json");
  });

  it("is left alone at the root, and other requests are never touched", () => {
    expect(staticCacheUrl("/__tsr/staticServerFnCache/abc.json", "/")).toBe("/__tsr/staticServerFnCache/abc.json");
    expect(staticCacheUrl("/api/search", "/Hoshimi/")).toBe("/api/search");
  });
});
