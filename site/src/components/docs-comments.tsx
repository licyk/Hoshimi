import { useTheme } from "fumadocs-ui/provider/base";
import { useEffect, useEffectEvent, useRef } from "react";

import {
  GISCUS_ATTRIBUTES,
  GISCUS_ORIGIN,
  giscusLanguage,
  giscusTerm,
  giscusTheme,
} from "@/lib/giscus";
import { useSiteCopy, useSiteLocale } from "@/lib/site-copy";

/**
 * giscus discussion for the current documentation page.
 *
 * The page route remounts this per path, so each page loads its own thread
 * from `location.pathname`, normalized by `giscusTerm`. Theme and language
 * changes are posted to the loaded frame instead of reloading it.
 */
export function DocsComments() {
  const containerRef = useRef<HTMLDivElement>(null);
  const { resolvedTheme } = useTheme();
  const locale = useSiteLocale();
  const copy = useSiteCopy();
  const theme = giscusTheme(resolvedTheme);
  const lang = giscusLanguage(locale);
  // `next-themes` reports no theme until it has read storage; waiting keeps
  // the frame from loading once in the wrong palette.
  const isThemeKnown = resolvedTheme !== undefined;

  const mount = useEffectEvent((container: HTMLDivElement) => {
    const script = document.createElement("script");
    script.src = `${GISCUS_ORIGIN}/client.js`;
    script.async = true;
    script.crossOrigin = "anonymous";
    for (const [name, value] of Object.entries(GISCUS_ATTRIBUTES)) {
      script.setAttribute(name, value);
    }
    script.setAttribute("data-term", giscusTerm(window.location.pathname));
    script.setAttribute("data-theme", theme);
    script.setAttribute("data-lang", lang);
    container.append(script);
  });

  useEffect(() => {
    const container = containerRef.current;
    if (!isThemeKnown || !container) return;

    mount(container);
    return () => container.replaceChildren();
  }, [isThemeKnown]);

  useEffect(() => {
    const frame = containerRef.current?.querySelector<HTMLIFrameElement>("iframe.giscus-frame");
    frame?.contentWindow?.postMessage({ giscus: { setConfig: { theme, lang } } }, GISCUS_ORIGIN);
  }, [theme, lang]);

  return (
    <section aria-label={copy.comments} className="docs-comments">
      <div ref={containerRef} />
    </section>
  );
}
