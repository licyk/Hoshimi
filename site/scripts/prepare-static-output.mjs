import { access, copyFile, readFile, writeFile } from "node:fs/promises";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

const publicDirectory = fileURLToPath(new URL("../.output/public/", import.meta.url));
const metadata = JSON.parse(
  await readFile(fileURLToPath(new URL("../content/docs/meta.json", import.meta.url)), "utf8"),
);
const documentSlugs = metadata.pages.filter((page) => !page.startsWith("---"));
const documents = documentSlugs.map((slug) =>
  slug === "index" ? "docs/index.html" : `docs/${slug}/index.html`,
);
const requiredFiles = [
  "_shell.html",
  "index.html",
  "api/search",
  ...documents,
];

await Promise.all(requiredFiles.map((file) => access(join(publicDirectory, file))));

await copyFile(join(publicDirectory, "_shell.html"), join(publicDirectory, "404.html"));

// One sitemap for search engines, on the canonical (Netlify) address whichever host builds it:
// the GitHub Pages copy points its pages there too.
const siteUrl = (process.env.VITE_SITE_URL || "https://hoshimi.netlify.app").replace(/\/+$/, "");
const urls = ["/", ...documentSlugs.map((slug) => (slug === "index" ? "/docs" : `/docs/${slug}`))];
await writeFile(
  join(publicDirectory, "sitemap.xml"),
  `<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n${urls
    .map((url) => `  <url><loc>${siteUrl}${url}</loc></url>`)
    .join("\n")}\n</urlset>\n`,
);
await writeFile(join(publicDirectory, "robots.txt"), `User-agent: *\nAllow: /\n\nSitemap: ${siteUrl}/sitemap.xml\n`);
