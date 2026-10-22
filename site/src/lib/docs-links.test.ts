import { readdirSync, readFileSync } from "node:fs";

import { describe, expect, it } from "vitest";

const docsDirectory = new URL("../../content/docs/", import.meta.url);
const meta = JSON.parse(readFileSync(new URL("meta.json", docsDirectory), "utf8")) as { pages: string[] };
const slugs = new Set(meta.pages.filter((page) => !page.startsWith("---")));
const files = readdirSync(docsDirectory).filter((name) => name.endsWith(".mdx"));

/** `/docs/<slug>` links in Markdown (`](/docs/…)`) and in components (`href="/docs/…"`). */
function docsLinks(source: string): string[] {
  const links = [...source.matchAll(/\]\((\/docs[^)\s#]*)/g), ...source.matchAll(/href=["'](\/docs[^"'#]*)/g)];
  return links.map((match) => match[1]);
}

describe("documentation links", () => {
  it("lists a page for every file, and a file for every listed page in both languages", () => {
    for (const slug of slugs) {
      expect(files, `${slug}.mdx is missing`).toContain(`${slug}.mdx`);
      expect(files, `${slug}.en.mdx is missing`).toContain(`${slug}.en.mdx`);
    }
    for (const file of files) {
      expect(slugs, `${file} is not in meta.json`).toContain(file.replace(/(\.en)?\.mdx$/, ""));
    }
  });

  it.each(files)("%s links only pages that exist", (file) => {
    const source = readFileSync(new URL(file, docsDirectory), "utf8");
    for (const link of docsLinks(source)) {
      const slug = link.replace(/^\/docs\/?/, "").replace(/\/$/, "") || "index";
      expect(slugs, `${file} links ${link}`).toContain(slug);
    }
  });
});
