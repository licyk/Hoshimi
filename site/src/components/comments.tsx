import { useTheme } from "fumadocs-ui/provider/base";
import { lazy, Suspense, useEffect, useState } from "react";

import { GISCUS } from "@/lib/site-config";
import { useSiteCopy, useSiteLocale } from "@/lib/site-copy";

/**
 * The page's discussion, at the end of a documentation page.
 *
 * It follows the site's own theme and language rather than the browser's: the theme toggle can
 * differ from the system, and a reader who chose English reads English. It loads and renders in
 * the browser only: the widget touches the DOM as it is imported, which the prerender has not got,
 * and the prerendered page and the first client render must agree.
 */
const Giscus = lazy(() => import("@giscus/react"));
export default function Comments({ term }: { term: string }) {
  const copy = useSiteCopy();
  const locale = useSiteLocale();
  const { resolvedTheme } = useTheme();
  const [mounted, setMounted] = useState(false);
  useEffect(() => setMounted(true), []);

  return (
    <section className="docs-comments" aria-label={copy.comments}>
      {mounted ? (
        <Suspense fallback={null}>
          <Giscus
          key={term}
          repo={GISCUS.repo}
          repoId={GISCUS.repoId}
          category={GISCUS.category}
          categoryId={GISCUS.categoryId}
          mapping="specific"
          term={term}
          strict="1"
          reactionsEnabled="1"
          emitMetadata="0"
          inputPosition="top"
          theme={resolvedTheme === "dark" ? "dark" : "light"}
          lang={locale === "en" ? "en" : "zh-CN"}
          loading="lazy"
          />
        </Suspense>
      ) : null}
    </section>
  );
}
