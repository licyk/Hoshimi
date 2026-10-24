import { shallowReactive } from 'vue';
import { themeKey } from '@/ui/markdownRich';

/**
 * Mermaid diagrams, drawn off the page and kept: the library is loaded the first time a reply holds
 * a diagram (it is large), draws one diagram at a time, and keeps each result (its SVG, or why it
 * could not) under the block's key, which names its source and the theme. It runs in its strictest
 * mode: no scripts, no clicks bound to code, its labels plain SVG text rather than HTML. Its colours
 * are the theme's own roles, read when it draws.
 *
 * Mermaid sizes a drawing as a share of its box (``width="100%"`` and its own width as a
 * ``max-width``), so a wide one shrinks to the column until its words cannot be read. A drawing is
 * kept at its own size instead (its ``viewBox``'s), which the page lays it out at a scale of.
 */
export interface Drawing {
  svg?: string;
  /** The drawing's own size, in CSS pixels; 0 when it states none. */
  width?: number;
  height?: number;
  error?: string;
}

/** ``svg`` sized as its ``viewBox`` (or the size it states in pixels), with Mermaid's share of the box taken off. */
export function naturalSize(svg: string): Required<Pick<Drawing, 'svg' | 'width' | 'height'>> {
  const open = /<svg\b[^>]*>/i.exec(svg);
  if (!open) return { svg, width: 0, height: 0 };
  const box = /\bviewBox\s*=\s*["']\s*[-\d.]+[\s,]+[-\d.]+[\s,]+([\d.]+)[\s,]+([\d.]+)\s*["']/i.exec(open[0]);
  const pixels = (name: string) => Number(new RegExp(String.raw`\s${name}\s*=\s*["']\s*([\d.]+)(px)?\s*["']`, 'i').exec(open[0])?.[1] ?? 0);
  const width = box ? Number(box[1]) : pixels('width');
  const height = box ? Number(box[2]) : pixels('height');
  if (!(width > 0 && height > 0)) return { svg, width: 0, height: 0 };
  const tag = open[0]
    .replace(/\s(width|height)\s*=\s*("[^"]*"|'[^']*')/gi, '')
    .replace(/max-width:\s*[^;"']*;?\s*/i, '')
    .replace(/\sstyle\s*=\s*(""|'')/i, '')
    .replace(/^<svg\b/i, `<svg width="${width}" height="${height}"`);
  return { svg: svg.slice(0, open.index) + tag + svg.slice(open.index + open[0].length), width, height };
}
export const drawings = shallowReactive(new Map<string, Drawing>());
const pending = new Set<string>();
let queue: Promise<unknown> = Promise.resolve();
let drawnWith = '';
let count = 0;

function themeVariables(): Record<string, string | boolean> {
  const style = getComputedStyle(document.documentElement);
  const role = (name: string) => style.getPropertyValue(`--md-sys-color-${name}`).trim();
  return {
    darkMode: document.documentElement.dataset.theme === 'dark',
    background: role('surface-container-low'),
    fontFamily: getComputedStyle(document.body).fontFamily,
    primaryColor: role('primary-container'),
    primaryTextColor: role('on-primary-container'),
    primaryBorderColor: role('primary'),
    secondaryColor: role('secondary-container'),
    secondaryTextColor: role('on-secondary-container'),
    secondaryBorderColor: role('secondary'),
    tertiaryColor: role('tertiary-container'),
    tertiaryTextColor: role('on-tertiary-container'),
    tertiaryBorderColor: role('tertiary'),
    mainBkg: role('primary-container'),
    nodeBorder: role('primary'),
    clusterBkg: role('surface-container-high'),
    clusterBorder: role('outline-variant'),
    lineColor: role('on-surface-variant'),
    textColor: role('on-surface'),
    titleColor: role('on-surface'),
    edgeLabelBackground: role('surface-container-highest'),
    noteBkgColor: role('tertiary-container'),
    noteTextColor: role('on-tertiary-container'),
    noteBorderColor: role('tertiary'),
    errorBkgColor: role('error-container'),
    errorTextColor: role('on-error-container'),
  };
}

/** Draws ``source`` under ``key`` unless it is drawn or being drawn; the result lands in ``drawings``. */
export function draw(key: string, source: string): void {
  if (drawings.has(key) || pending.has(key)) return;
  pending.add(key);
  queue = queue
    .then(async () => {
      const { default: mermaid } = await import('mermaid');
      if (drawnWith !== themeKey.value) {
        mermaid.initialize({
          startOnLoad: false, securityLevel: 'strict', theme: 'base', themeVariables: themeVariables(),
          htmlLabels: false, flowchart: { htmlLabels: false }, suppressErrorRendering: true,
        });
        drawnWith = themeKey.value;
      }
      const id = `hoshimi-mermaid-${++count}`;
      try {
        const { svg } = await mermaid.render(id, source);
        drawings.set(key, naturalSize(svg));
      } finally {
        // A failed drawing can leave its scratch element behind.
        document.getElementById(id)?.remove();
        document.getElementById(`d${id}`)?.remove();
      }
    })
    .catch((error: unknown) => drawings.set(key, { error: (error instanceof Error ? error.message : String(error)).split('\n')[0] || 'error' }))
    .finally(() => pending.delete(key));
}
