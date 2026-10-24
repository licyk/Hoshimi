import { describe, expect, it } from "vitest";

import { GISCUS_ATTRIBUTES, giscusLanguage, giscusTerm, giscusTheme } from "@/lib/giscus";
import { LOCALES } from "@/lib/i18n";

describe("giscus", () => {
  it("uses Hoshimi's discussions, mapped by the page's own term", () => {
    expect(GISCUS_ATTRIBUTES["data-repo"]).toBe("licyk/Hoshimi");
    expect(GISCUS_ATTRIBUTES["data-category"]).toBe("Comment");
    expect(GISCUS_ATTRIBUTES["data-mapping"]).toBe("specific");
    expect(GISCUS_ATTRIBUTES["data-strict"]).toBe("1");
  });

  it("maps one discussion to each page, with or without a trailing slash", () => {
    expect(giscusTerm("/docs/quick-start", "/")).toBe("docs/quick-start");
    expect(giscusTerm("/docs/quick-start/", "/")).toBe("docs/quick-start");
    expect(giscusTerm("/docs/", "/")).toBe("docs");
    expect(giscusTerm("/", "/")).toBe("index");
  });

  it("gives a page the same discussion on GitHub Pages (/Hoshimi/) and at a root", () => {
    expect(giscusTerm("/Hoshimi/docs/guide-agent", "/Hoshimi/")).toBe("docs/guide-agent");
    expect(giscusTerm("/Hoshimi/docs/guide-agent/", "/Hoshimi")).toBe("docs/guide-agent");
    expect(giscusTerm("/Hoshimi/", "/Hoshimi/")).toBe("index");
    expect(giscusTerm("/Hoshimix/docs", "/Hoshimi/")).toBe("Hoshimix/docs");
  });

  it("gives both languages of a page the same discussion", () => {
    expect(giscusTerm("/en/docs/guide-agent", "/")).toBe("docs/guide-agent");
    expect(giscusTerm("/Hoshimi/zh-CN/docs/guide-agent", "/Hoshimi/")).toBe("docs/guide-agent");
  });

  it("follows the site's resolved theme", () => {
    expect(giscusTheme("dark")).toBe("dark");
    expect(giscusTheme("light")).toBe("light");
    expect(giscusTheme(undefined)).toBe("light");
  });

  it("has a giscus language for every site locale", () => {
    for (const locale of LOCALES) expect(giscusLanguage(locale)).toBeTruthy();
    expect(giscusLanguage("zh-CN")).toBe("zh-CN");
    expect(giscusLanguage("en")).toBe("en");
  });
});
