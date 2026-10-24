<script lang="ts">
import MarkdownIt from 'markdown-it';
import type Token from 'markdown-it/lib/token.mjs';
import '@/ui/codeBlock.css';
import { highlight, languageName } from '@/ui/highlight';
import { mathPlugin } from '@/ui/markdownMath';
import {
  DEFAULT_LABELS, allowedMedia, blockModes, fill, hash, hostOf, isRemote, mediaKind, previewDocument,
  type BlockMode, type MarkdownLabels, type RemoteMedia,
} from '@/ui/markdownRich';
import { drawings } from '@/ui/mermaid';

/**
 * One renderer for every reply. The text is a model's, so it is never trusted: raw HTML is shown as
 * text (``html: false``), and markdown-it already refuses ``javascript:``, ``vbscript:`` and most
 * ``data:`` links. Single newlines break the line, as they do in a chat. Links open beside the
 * app and give the page they open no hold on it. Code blocks and tables scroll sideways on their
 * own (``.app-scroll-x``) rather than widen the conversation.
 *
 * A code block is highlighted when its fence names a language this build knows (``highlight.ts``).
 * Its head is a toolbar (``codeBlock.css``): an icon for what it holds and its language by name
 * (``Text`` for none), then its buttons, a copy button last. The button is plain HTML in the rendered
 * string, so a click on it is caught on the whole reply: the code goes to the clipboard, the button
 * shows a check (and says ``copiedLabel``) for a moment, and ``copied`` or ``copyFailed`` lets the
 * page say so too. The check is set on the button in place rather than rendered, so a keyboard
 * user's focus stays on it.
 *
 * Beyond text (see ``markdownRich.ts`` for the state these share):
 *
 * - **Math**: TeX between ``$``, ``$$``, ``\(`` or ``\[`` marks, typeset by KaTeX (``markdownMath.ts``).
 * - **Images, video and audio**: ``![…](src)``, by the address's type or extension. One written into
 *   the reply (``data:``) shows at once; one on another site waits behind a button naming the site,
 *   unless the reader chose to always load them. An image is a button that opens it in the page's
 *   image viewer (any ``[data-view]`` is one: the page catches the click).
 * - **Diagrams**: a ``mermaid`` block is drawn once it is closed (``mermaid.ts``), an ``svg`` block is
 *   shown as an image (its scripts never run), and an ``html`` block offers a preview in a frame with
 *   no origin and no network. Each keeps its code a press away, and its copy button. A drawn diagram
 *   opens at its own size, or smaller to fit the column but never too small to read, in a frame at
 *   most ``DIAGRAM_HEIGHT`` tall; larger than its frame, it fades at the edges where there is more and
 *   is dragged about. It zooms with Ctrl or ⌘ and the wheel, a pinch, or the buttons in its head (out,
 *   its scale, in, fit, open); a plain wheel still scrolls the conversation, and a press that did not
 *   drag opens it in the viewer.
 *
 * The reply renders block by block: as it streams, only the block being written changes, so a
 * diagram or a preview above it is not drawn again.
 */
const md = new MarkdownIt({ html: false, linkify: true, breaks: true });
const escape = md.utils.escapeHtml;
md.use(mathPlugin);
// Video and audio written into the reply, besides the images markdown-it lets through.
const validate = md.validateLink.bind(md);
md.validateLink = (url) => validate(url) || /^data:(video|audio)\/[\w.+-]+;base64,/i.test(url.trim());

/** Lucide icons, as the buttons are markup rather than components; CSS shows the one that applies. */
const ICON = (className: string, shapes: string) =>
  `<svg class="${className}" aria-hidden="true" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">${shapes}</svg>`;
const COPY_ICONS =
  ICON('icon-copy', '<rect width="14" height="14" x="8" y="8" rx="2" ry="2"/><path d="M4 16c-1.1 0-2-.9-2-2V4c0-1.1.9-2 2-2h10c1.1 0 2 .9 2 2"/>') +
  ICON('icon-check', '<path d="M20 6 9 17l-5-5"/>');
const IMAGE_SHAPES = '<rect width="18" height="18" x="3" y="3" rx="2" ry="2"/><circle cx="9" cy="9" r="2"/><path d="m21 15-3.086-3.086a2 2 0 0 0-2.828 0L6 21"/>';
const IMAGE_ICON = ICON('icon-media', IMAGE_SHAPES);
const PLAY_ICON = ICON('icon-media', '<polygon points="6 3 20 12 6 21 6 3"/>');
const ZOOM_ICONS = {
  in: ICON('', '<circle cx="11" cy="11" r="8"/><path d="m21 21-4.35-4.35"/><path d="M11 8v6"/><path d="M8 11h6"/>'),
  out: ICON('', '<circle cx="11" cy="11" r="8"/><path d="m21 21-4.35-4.35"/><path d="M8 11h6"/>'),
  fit: ICON('', '<path d="M3 7V5a2 2 0 0 1 2-2h2"/><path d="M17 3h2a2 2 0 0 1 2 2v2"/><path d="M21 17v2a2 2 0 0 1-2 2h-2"/><path d="M7 21H5a2 2 0 0 1-2-2v-2"/>'),
};
const OPEN_ICON = ICON('', '<polyline points="15 3 21 3 21 9"/><polyline points="9 21 3 21 3 15"/><line x1="21" x2="14" y1="3" y2="10"/><line x1="3" x2="10" y1="21" y2="14"/>');
/** What the head's icon says a block holds: code, a command, a diff, a diagram, an image, a page. */
const KIND_ICONS = {
  code: '<path d="m16 18 6-6-6-6"/><path d="m8 6-6 6 6 6"/>',
  terminal: '<polyline points="4 17 10 11 4 5"/><line x1="12" x2="20" y1="19" y2="19"/>',
  diff: '<circle cx="18" cy="18" r="3"/><circle cx="6" cy="6" r="3"/><path d="M13 6h3a2 2 0 0 1 2 2v7"/><path d="M11 18H8a2 2 0 0 1-2-2V9"/>',
  diagram: '<rect width="8" height="8" x="3" y="3" rx="2"/><path d="M7 11v4a2 2 0 0 0 2 2h4"/><rect width="8" height="8" x="13" y="13" rx="2"/>',
  image: IMAGE_SHAPES,
  page: '<rect x="2" y="4" width="20" height="16" rx="2"/><path d="M10 4v4"/><path d="M2 8h20"/><path d="M6 4v4"/>',
  text: '<path d="M17 6.1H3"/><path d="M21 12.1H3"/><path d="M15.1 18H3"/>',
};
const TERMINALS = new Set(['bash', 'sh', 'shell', 'zsh', 'fish', 'console', 'shellsession', 'powershell', 'ps1', 'pwsh', 'ps', 'cmd', 'bat', 'batch']);
function kindOf(language: string): keyof typeof KIND_ICONS {
  const lower = language.toLowerCase();
  if (!lower || lower === 'text' || lower === 'txt' || lower === 'plaintext' || lower === 'plain') return 'text';
  if (TERMINALS.has(lower)) return 'terminal';
  if (lower === 'diff' || lower === 'patch') return 'diff';
  if (lower === 'mermaid') return 'diagram';
  if (lower === 'svg') return 'image';
  if (lower === 'html' || lower === 'htm') return 'page';
  return 'code';
}
/** How long a copy button shows its check. */
export const COPIED_MS = 1500;

export interface RenderOptions {
  copyLabel?: string;
  labels?: MarkdownLabels;
  remote?: RemoteMedia;
  /** The theme a diagram is drawn in (``themeKey``). */
  theme?: string;
}
interface Env {
  copyLabel: string;
  labels: MarkdownLabels;
  remote: RemoteMedia;
  theme: string;
  /** The source's lines: whether a fence is closed yet. */
  lines: string[];
}

/** Diagrams waiting to be drawn, by key: their source. The component draws what its render left a placeholder for. */
export const diagramSources = new Map<string, string>();

interface BlockParts {
  /** Buttons in the head, before the copy button. */
  tools?: string;
  /** Words after the language in the head (the kind of diagram). */
  subtitle?: string;
  /** What shows above the code (a diagram, an image, a preview). */
  body?: string;
  hideCode?: boolean;
}

/**
 * A block's head is a toolbar: an icon for what it holds and its language (``Text`` for a block that
 * names none), then its buttons, copy last.
 */
function codeBlock(code: string, info: string, env: Env, parts: BlockParts = {}): string {
  const language = info.trim().split(/\s+/)[0] ?? '';
  const highlighted = highlight(code, language);
  const label = escape(env.copyLabel);
  const name = languageName(language) || env.labels.plainText;
  return (
    '<div class="code-block">' +
    `<div class="code-head"><span class="code-title">${ICON('code-icon', KIND_ICONS[kindOf(language)])}<span class="code-lang">${escape(name)}</span>` +
    `${parts.subtitle ? `<span class="code-sub">${escape(parts.subtitle)}</span>` : ''}</span><span class="code-tools">${parts.tools ?? ''}` +
    `<button type="button" class="code-copy" data-copy aria-label="${label}" title="${label}">${COPY_ICONS}</button></span></div>` +
    (parts.body ?? '') +
    `<pre class="app-scroll app-scroll-x app-scroll-auto"${parts.hideCode ? ' hidden' : ''}><code class="hljs${highlighted === null ? '' : ` language-${escape(language)}`}">${highlighted ?? escape(code)}</code></pre>` +
    '</div>\n'
  );
}

type Rich = 'mermaid' | 'svg' | 'html';
const RICH: Record<string, Rich> = { mermaid: 'mermaid', svg: 'svg', html: 'html', htm: 'html' };

/** Whether a fence has its closing mark yet (a reply still streaming may not). */
function closed(token: Token, env: Env): boolean {
  const [start, end] = token.map ?? [0, 0];
  const last = (env.lines[end - 1] ?? '').replace(/^[\s>]*/, '').trimEnd();
  return end - 1 > start && last.length >= token.markup.length && [...last].every((c) => c === token.markup[0]);
}

const viewButton = (url: string, alt: string, env: Env, inner: string, className = 'media-view') =>
  `<button type="button" class="${className}" data-view="${escape(url)}" data-alt="${escape(alt)}" aria-label="${escape(env.labels.openImage + (alt ? `: ${alt}` : ''))}">${inner}</button>`;
/**
 * An SVG as an image's address. As an image an SVG has only the size it states: one that states a
 * share of its box (Mermaid's ``width="100%"``) or none is given the size of its ``viewBox``.
 */
function svgUrl(svg: string): string {
  const open = /<svg\b[^>]*>/i.exec(svg);
  const box = open && /\bviewBox\s*=\s*["']\s*[-\d.]+[\s,]+[-\d.]+[\s,]+([\d.]+)[\s,]+([\d.]+)\s*["']/i.exec(open[0]);
  if (open && box && !/\bwidth\s*=\s*["']\s*[\d.]+(px)?\s*["']/i.test(open[0])) {
    const tag = open[0].replace(/\s(width|height)\s*=\s*("[^"]*"|'[^']*')/gi, '').replace(/^<svg\b/i, `<svg width="${box[1]}" height="${box[2]}"`);
    svg = svg.slice(0, open.index) + tag + svg.slice(open.index + open[0].length);
  }
  return `data:image/svg+xml;charset=utf-8,${encodeURIComponent(svg)}`;
}

/** The kinds of diagram, by the word a Mermaid source starts with, as its head names them. */
const DIAGRAM_TYPES: Record<string, string> = {
  graph: 'flowchart', flowchart: 'flowchart', 'flowchart-elk': 'flowchart', sequencediagram: 'sequence', classdiagram: 'class', 'classdiagram-v2': 'class',
  statediagram: 'state', 'statediagram-v2': 'state', erdiagram: 'entity relationship', gantt: 'gantt', pie: 'pie', journey: 'user journey',
  mindmap: 'mindmap', timeline: 'timeline', gitgraph: 'git graph', quadrantchart: 'quadrant', 'xychart-beta': 'xy chart', xychart: 'xy chart',
  'sankey-beta': 'sankey', sankey: 'sankey', 'block-beta': 'block', block: 'block', requirementdiagram: 'requirement', 'architecture-beta': 'architecture',
  'packet-beta': 'packet', packet: 'packet', kanban: 'kanban', 'radar-beta': 'radar', c4context: 'C4', c4container: 'C4', c4component: 'C4',
  c4dynamic: 'C4', c4deployment: 'C4', zenuml: 'sequence', treemap: 'treemap', 'treemap-beta': 'treemap',
};
/** What kind of diagram ``source`` is: its first word past any front matter and comments. */
export function diagramType(source: string): string {
  const body = source.replace(/^\s*---\n[\s\S]*?\n---\s*\n/, '');
  const line = body.split('\n').map((l) => l.trim()).find((l) => l && !l.startsWith('%%')) ?? '';
  const word = /^[\w-]+/.exec(line)?.[0] ?? '';
  return DIAGRAM_TYPES[word.toLowerCase()] ?? word;
}

/**
 * A drawn diagram, at its own size (``data-width``, ``data-height``) in a frame the component lays it
 * out in (``layoutDiagram``), with its zoom in the block's head: out, the scale (a press shows it at
 * its own size), in, fit, and open in the viewer.
 */
function diagram(source: string, env: Env): Pick<BlockParts, 'body' | 'hideCode' | 'tools'> {
  const key = hash(`${env.theme}\n${source}`);
  const drawing = drawings.get(key);
  if (!drawing) {
    diagramSources.set(key, source);
    return { body: `<div class="diagram pending" data-mermaid="${key}" role="status">${escape(env.labels.drawing)}</div>`, hideCode: true };
  }
  if (drawing.error !== undefined) return { body: `<p class="diagram-error" role="note">${escape(fill(env.labels.diagramFailed, { reason: drawing.error }))}</p>` };
  const tool = (zoom: keyof typeof ZOOM_ICONS, label: string) =>
    `<button type="button" class="code-tool" data-zoom="${zoom}" aria-label="${escape(label)}" title="${escape(label)}">${ZOOM_ICONS[zoom]}</button>`;
  const actual = escape(env.labels.actualSize);
  const tools =
    `<span class="code-zoom">${tool('out', env.labels.zoomOut)}` +
    `<button type="button" class="code-zoom-level" data-zoom="actual" aria-label="${actual}" title="${actual}">100%</button>` +
    `${tool('in', env.labels.zoomIn)}${tool('fit', env.labels.resetZoom)}</span>` +
    `<button type="button" class="code-tool" data-open aria-label="${escape(env.labels.openImage)}" title="${escape(env.labels.openImage)}">${OPEN_ICON}</button>` +
    '<span class="code-sep" aria-hidden="true"></span>';
  return {
    body:
      `<div class="diagram-frame" data-diagram="${key}" data-width="${drawing.width ?? 0}" data-height="${drawing.height ?? 0}">` +
      `<div class="diagram app-scroll app-scroll-x app-scroll-auto">${viewButton(svgUrl(drawing.svg!), env.labels.diagram, env, drawing.svg!, 'diagram-view')}</div>` +
      '</div>',
    hideCode: true,
    tools,
  };
}

function richBlock(kind: Rich, token: Token, env: Env): string {
  const source = token.content;
  const key = hash(`${kind}\n${source}`);
  const subtitle = kind === 'mermaid' ? diagramType(source) : undefined;
  if (!closed(token, env)) return codeBlock(source, token.info, env, { subtitle });
  const mode: BlockMode = blockModes.get(key) ?? (kind === 'html' ? 'code' : 'rendered');
  const other: BlockMode = mode === 'rendered' ? 'code' : 'rendered';
  const label = mode === 'rendered' ? env.labels.code : { mermaid: env.labels.diagram, svg: env.labels.image, html: env.labels.preview }[kind];
  const tools = `<button type="button" class="code-toggle" data-toggle="${key}" data-mode="${other}">${escape(label)}</button>`;
  if (mode === 'code') return codeBlock(source, token.info, env, { tools, subtitle });
  if (kind === 'mermaid') {
    const drawn = diagram(source, env);
    return codeBlock(source, token.info, env, { ...drawn, tools: (drawn.tools ?? '') + tools, subtitle });
  }
  if (kind === 'svg') {
    const url = svgUrl(source);
    return codeBlock(source, token.info, env, { tools, hideCode: true, body: `<div class="svg-figure">${viewButton(url, env.labels.image, env, `<img src="${escape(url)}" alt="${escape(env.labels.image)}">`)}</div>` });
  }
  const frame =
    `<iframe class="html-preview" data-frame="${key}" sandbox="allow-scripts" referrerpolicy="no-referrer" ` +
    `title="${escape(env.labels.previewTitle)}" srcdoc="${escape(previewDocument(source))}"></iframe>`;
  return codeBlock(source, token.info, env, { tools, hideCode: true, body: frame });
}

const link = md.renderer.rules.link_open ?? ((tokens, i, options, _env, self) => self.renderToken(tokens, i, options));
md.renderer.rules.link_open = (tokens, i, options, env, self) => {
  tokens[i].attrSet('target', '_blank');
  tokens[i].attrSet('rel', 'noopener noreferrer');
  return link(tokens, i, options, env, self);
};
md.renderer.rules.fence = (tokens, i, _options, env: Env) => {
  const kind = RICH[(tokens[i].info.trim().split(/\s+/)[0] ?? '').toLowerCase()];
  return kind ? richBlock(kind, tokens[i], env) : codeBlock(tokens[i].content, tokens[i].info, env);
};
md.renderer.rules.code_block = (tokens, i, _options, env) => codeBlock(tokens[i].content, '', env);
md.renderer.rules.table_open = () => '<div class="table app-scroll app-scroll-x app-scroll-auto"><table>\n';
md.renderer.rules.table_close = () => '</table></div>\n';

/** Whether the inline token at ``i`` sits inside a link: an image there stays the link's, not a button. */
function inLink(tokens: Token[], i: number): boolean {
  let depth = 0;
  for (let j = 0; j < i; j++) depth += tokens[j].type === 'link_open' ? 1 : tokens[j].type === 'link_close' ? -1 : 0;
  return depth > 0;
}
md.renderer.rules.image = (tokens, i, options, env: Env, self) => {
  const token = tokens[i];
  const src = token.attrGet('src') ?? '';
  const alt = self.renderInlineAsText(token.children ?? [], options, env);
  const kind = mediaKind(src);
  if (isRemote(src) && env.remote === 'ask' && !allowedMedia.has(src)) {
    const label = fill({ image: env.labels.loadImage, video: env.labels.loadVideo, audio: env.labels.loadAudio }[kind], { host: hostOf(src) });
    return `<button type="button" class="media-gate" data-load="${escape(src)}" title="${escape(src)}">${kind === 'image' ? IMAGE_ICON : PLAY_ICON}<span>${escape(label)}</span></button>`;
  }
  if (kind === 'video') return `<video class="media-video" src="${escape(src)}" controls preload="metadata" playsinline></video>`;
  if (kind === 'audio') return `<audio class="media-audio" src="${escape(src)}" controls preload="metadata"></audio>`;
  const title = token.attrGet('title');
  const img = `<img src="${escape(src)}" alt="${escape(alt)}"${title ? ` title="${escape(title)}"` : ''} loading="lazy" decoding="async" referrerpolicy="no-referrer">`;
  return inLink(tokens, i) ? img : viewButton(src, alt, env, img);
};

/** The source in its top-level blocks, each rendered on its own (a list, a table, a code block…). */
export function renderBlocks(source: string, options: RenderOptions = {}): string[] {
  const env: Env = {
    copyLabel: options.copyLabel ?? 'Copy', labels: options.labels ?? DEFAULT_LABELS, remote: options.remote ?? 'ask', theme: options.theme ?? '',
    lines: source.split(/\r\n|\r|\n/),
  };
  const tokens = md.parse(source, env);
  const blocks: string[] = [];
  let start = 0;
  let depth = 0;
  tokens.forEach((token, i) => {
    depth += token.nesting;
    if (depth !== 0) return;
    blocks.push(md.renderer.render(tokens.slice(start, i + 1), md.options, env));
    start = i + 1;
  });
  return blocks;
}

export const renderMarkdown = (source: string, copyLabel = 'Copy', options: RenderOptions = {}): string => renderBlocks(source, { ...options, copyLabel }).join('');
</script>

<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, onUpdated, ref } from 'vue';
import { listenToFrames, sizeFrames, themeKey, useMarkdownContext, watchThemeKey } from '@/ui/markdownRich';
import { draw } from '@/ui/mermaid';

const props = defineProps<{ source: string; copyLabel: string; copiedLabel: string }>();
const emit = defineEmits<{ copied: [code: string]; copyFailed: [] }>();
const { remoteMedia, labels } = useMarkdownContext();
watchThemeKey();
listenToFrames();
const blocks = computed(() => renderBlocks(props.source, { copyLabel: props.copyLabel, labels: labels.value, remote: remoteMedia.value, theme: themeKey.value }));
const root = ref<HTMLElement | null>(null);

// After each render: draw the diagrams it left a place for, lay out the ones drawn, and give previews their height back.
function settle() {
  const el = root.value;
  if (!el) return;
  for (const holder of el.querySelectorAll<HTMLElement>('[data-mermaid]')) {
    const key = holder.dataset.mermaid!;
    const source = diagramSources.get(key);
    if (source !== undefined) draw(key, source);
  }
  // A frame the render put in anew is laid out once; the observer sees to it when its column changes.
  for (const frame of el.querySelectorAll<HTMLElement>('.diagram-frame:not(.laid-out)')) {
    frame.classList.add('laid-out');
    layoutDiagram(frame);
    const viewport = parts(frame).viewport;
    if (viewport) resizes?.observe(viewport);
  }
  sizeFrames(el);
}
onMounted(settle);
onUpdated(settle);

const timers = new WeakMap<Element, ReturnType<typeof setTimeout>>();
function showCopied(button: Element) {
  clearTimeout(timers.get(button));
  button.classList.add('copied');
  button.setAttribute('aria-label', props.copiedLabel);
  button.setAttribute('title', props.copiedLabel);
  timers.set(button, setTimeout(() => {
    button.classList.remove('copied');
    button.setAttribute('aria-label', props.copyLabel);
    button.setAttribute('title', props.copyLabel);
  }, COPIED_MS));
}

/** A button the render replaced keeps the keyboard's place: focus goes to the one standing where it was. */
async function refocus(selector: string) {
  await nextTick();
  root.value?.querySelector<HTMLElement>(selector)?.focus();
}

// -- a drawn diagram, laid out at a scale and moved about in its frame --------------------------- #
/**
 * How each diagram shows, by its key (its source and theme), so a render that replaces its frame
 * keeps it: at the scale it opens at (``auto``: its own size, or smaller to fit the column but never
 * so small its words cannot be read), fitted whole (``fit``), or as the reader zoomed it (``manual``).
 * The frame is as tall as the drawing up to ``DIAGRAM_HEIGHT``; a drawing larger than its frame is
 * dragged about in it (or scrolled sideways). It is laid out at its scale, not scaled, so it stays sharp.
 */
interface Zoom {
  mode: 'auto' | 'fit' | 'manual';
  scale: number;
  /** The frame's height: it grows as the reader zooms in (up to the most), and is worked out again on fit. */
  height: number;
}
const zooms = new Map<string, Zoom>();
const DIAGRAM_MAX = 4;
const ZOOM_STEP = 1.25;
const DIAGRAM_HEIGHT = 480;
/** The smallest scale a diagram opens at: below it, its words are too small to read. */
const READABLE = 0.6;
const parts = (frame: HTMLElement) => ({
  viewport: frame.querySelector<HTMLElement>('.diagram'),
  svg: frame.querySelector<SVGElement>('.diagram-view svg'),
  width: Number(frame.dataset.width) || 0,
  height: Number(frame.dataset.height) || 0,
});
/** The scale that shows all of a diagram in its column and at most ``DIAGRAM_HEIGHT`` tall, never above its own size. */
function fitScale(frame: HTMLElement): number {
  const { viewport, width, height } = parts(frame);
  const room = viewport?.clientWidth ?? 0;
  return Math.min(1, room > 0 ? room / width : 1, DIAGRAM_HEIGHT / height);
}
function opening(frame: HTMLElement, mode: 'auto' | 'fit'): Zoom {
  const fit = fitScale(frame);
  const scale = mode === 'fit' ? fit : Math.min(1, Math.max(fit, READABLE));
  return { mode, scale, height: Math.min(DIAGRAM_HEIGHT, parts(frame).height * scale) };
}

function paint(frame: HTMLElement, z: Zoom) {
  const { viewport, svg, width, height } = parts(frame);
  if (!viewport || !svg) return;
  svg.style.width = `${width * z.scale}px`;
  svg.style.height = `${height * z.scale}px`;
  viewport.style.height = `${Math.max(24, z.height)}px`;
  // A scrollbar along the foot takes its height from the drawing's: the frame gives it back.
  const bar = viewport.offsetHeight - viewport.clientHeight;
  if (bar > 0) viewport.style.height = `${Math.max(24, z.height) + bar}px`;
  const room = viewport.clientWidth;
  frame.classList.toggle('pannable', (room > 0 && width * z.scale > room + 1) || height * z.scale > z.height + 1);
  frame.classList.toggle('zoomed', z.mode === 'manual');
  const head = frame.closest('.code-block')?.querySelector('.code-head');
  const level = head?.querySelector('[data-zoom="actual"]');
  if (level) level.textContent = `${Math.round(z.scale * 100)}%`;
  const fit = fitScale(frame);
  head?.querySelector<HTMLButtonElement>('[data-zoom="out"]')?.toggleAttribute('disabled', z.scale <= Math.min(fit, 1) + 1e-3);
  head?.querySelector<HTMLButtonElement>('[data-zoom="in"]')?.toggleAttribute('disabled', z.scale >= DIAGRAM_MAX - 1e-3);
  fade(frame);
}
/** Lays a diagram out as it last showed, or as it opens; one that was only opened or fitted is fitted to the column it has now. */
function layoutDiagram(frame: HTMLElement) {
  const key = frame.dataset.diagram!;
  if (!parts(frame).width || !parts(frame).height) return;
  const kept = zooms.get(key);
  const z = kept?.mode === 'manual' ? kept : opening(frame, kept?.mode ?? 'auto');
  zooms.set(key, z);
  paint(frame, z);
}
const zoomOf = (frame: HTMLElement): Zoom => zooms.get(frame.dataset.diagram!) ?? opening(frame, 'auto');

/** Zoom a diagram to ``scale`` about a point of the window (its frame's middle by default), which stays where it is. */
function zoomDiagram(frame: HTMLElement, scale: number, at?: { x: number; y: number }) {
  const { viewport, svg, height } = parts(frame);
  if (!viewport || !svg) return;
  const z = zoomOf(frame);
  const next = Math.min(DIAGRAM_MAX, Math.max(Math.min(fitScale(frame), 1), scale));
  const box = viewport.getBoundingClientRect();
  const point = at ?? { x: box.left + box.width / 2, y: box.top + box.height / 2 };
  const before = svg.getBoundingClientRect();
  const zoom: Zoom = { mode: 'manual', scale: next, height: Math.min(DIAGRAM_HEIGHT, Math.max(z.height, height * next)) };
  zooms.set(frame.dataset.diagram!, zoom);
  paint(frame, zoom);
  if (!before.width || !before.height) return;
  // The share of the drawing under the point before is put back under it.
  const after = svg.getBoundingClientRect();
  viewport.scrollLeft += after.left + ((point.x - before.left) / before.width) * after.width - point.x;
  viewport.scrollTop += after.top + ((point.y - before.top) / before.height) * after.height - point.y;
}
function fitDiagram(frame: HTMLElement) {
  const z = opening(frame, 'fit');
  zooms.set(frame.dataset.diagram!, z);
  paint(frame, z);
}
/** A drawing that goes on past its frame's edge fades out at that edge, so the reader sees there is more of it. */
const FADE = 28;
function fade(frame: HTMLElement) {
  const viewport = parts(frame).viewport;
  if (!viewport) return;
  const { scrollLeft: left, scrollTop: top, clientWidth: width, clientHeight: height, scrollWidth, scrollHeight } = viewport;
  const edges = [
    left > 1 && 'to right',
    left + width < scrollWidth - 1 && 'to left',
    top > 1 && 'to bottom',
    top + height < scrollHeight - 1 && 'to top',
  ].filter(Boolean);
  const mask = edges.map((side) => `linear-gradient(${side}, transparent, #000 ${FADE}px)`).join(', ');
  viewport.style.setProperty('mask-image', mask);
  viewport.style.setProperty('-webkit-mask-image', mask);
  viewport.style.setProperty('mask-composite', edges.length > 1 ? 'intersect' : '');
  viewport.style.setProperty('-webkit-mask-composite', edges.length > 1 ? 'source-in' : '');
}
function onScroll(event: Event) {
  const target = event.target as Element;
  if (target.classList?.contains('diagram')) {
    const frame = frameAt(target);
    if (frame) fade(frame);
  }
}
const frameAt = (target: EventTarget | null) => (target as Element | null)?.closest?.<HTMLElement>('.diagram-frame') ?? null;
/** The diagram a button in a block's head is for. */
const frameOf = (button: Element) => button.closest('.code-block')?.querySelector<HTMLElement>('.diagram-frame') ?? null;

/** A frame whose column changes width fits its diagram to it again (unless the reader zoomed it). */
const widths = new WeakMap<Element, number>();
const resizes =
  typeof ResizeObserver === 'undefined'
    ? null
    : new ResizeObserver((entries) => {
        for (const entry of entries) {
          const width = Math.round(entry.contentRect.width);
          if (widths.get(entry.target) === width) continue;
          widths.set(entry.target, width);
          const frame = frameAt(entry.target);
          if (frame) layoutDiagram(frame);
        }
      });
onBeforeUnmount(() => resizes?.disconnect());

/** With Ctrl or ⌘ (a trackpad's pinch sends that too) the wheel zooms a diagram; alone it scrolls the page. */
function onWheel(event: WheelEvent) {
  const frame = frameAt(event.target);
  if (!frame || !(event.ctrlKey || event.metaKey)) return;
  event.preventDefault();
  const delta = Math.max(-50, Math.min(50, event.deltaY));
  zoomDiagram(frame, zoomOf(frame).scale * Math.exp(-delta * 0.006), { x: event.clientX, y: event.clientY });
}

/*
 * Pointers on a diagram: two pinch it; one drags it about when it is larger than its frame (a finger
 * only once the reader zoomed it: before that a finger scrolls the page, or the frame sideways). A
 * press that moved is not a click.
 */
const pointers = new Map<number, { x: number; y: number }>();
let gesture: { frame: HTMLElement; start: { x: number; y: number }; moved: boolean; pinch: { distance: number; scale: number } | null } | null = null;
let swallowClick = false;
const spread = () => {
  const [a, b] = [...pointers.values()];
  return Math.hypot(a.x - b.x, a.y - b.y);
};
function onPointerDown(event: PointerEvent) {
  const frame = frameAt(event.target);
  if (!frame || (gesture && gesture.frame !== frame)) return;
  pointers.set(event.pointerId, { x: event.clientX, y: event.clientY });
  gesture ??= { frame, start: { x: event.clientX, y: event.clientY }, moved: false, pinch: null };
  if (pointers.size === 2) gesture.pinch = { distance: spread(), scale: zoomOf(frame).scale };
  const drags = frame.classList.contains('pannable') && (event.pointerType !== 'touch' || frame.classList.contains('zoomed'));
  if (pointers.size === 2 || drags) frame.setPointerCapture?.(event.pointerId);
}
function onPointerMove(event: PointerEvent) {
  const last = pointers.get(event.pointerId);
  if (!last || !gesture) return;
  pointers.set(event.pointerId, { x: event.clientX, y: event.clientY });
  if (Math.hypot(event.clientX - gesture.start.x, event.clientY - gesture.start.y) > 4) gesture.moved = true;
  const frame = gesture.frame;
  if (gesture.pinch && pointers.size === 2) {
    const [a, b] = [...pointers.values()];
    zoomDiagram(frame, (gesture.pinch.scale * spread()) / gesture.pinch.distance, { x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 });
  } else if (pointers.size === 1 && frame.classList.contains('pannable') && (event.pointerType !== 'touch' || frame.classList.contains('zoomed'))) {
    const viewport = parts(frame).viewport!;
    viewport.scrollLeft -= event.clientX - last.x;
    viewport.scrollTop -= event.clientY - last.y;
    frame.classList.toggle('dragging', gesture.moved);
  }
}
function onPointerUp(event: PointerEvent) {
  if (!pointers.delete(event.pointerId) || !gesture) return;
  if (pointers.size < 2) gesture.pinch = null;
  if (pointers.size) return;
  gesture.frame.classList.remove('dragging');
  // The click that follows a drag or a pinch must not open the viewer; one that never comes is forgotten.
  if (gesture.moved) {
    swallowClick = true;
    setTimeout(() => (swallowClick = false), 0);
  }
  gesture = null;
}

async function onClick(event: MouseEvent) {
  const target = event.target as Element;
  if (swallowClick) {
    swallowClick = false;
    event.preventDefault();
    event.stopPropagation();
    return;
  }
  const zoom = target.closest<HTMLElement>('[data-zoom]');
  const frame = zoom && frameOf(zoom);
  if (zoom && frame) {
    const scale = zoomOf(frame).scale;
    const step = zoom.dataset.zoom;
    if (step === 'fit') fitDiagram(frame);
    else zoomDiagram(frame, step === 'in' ? scale * ZOOM_STEP : step === 'out' ? scale / ZOOM_STEP : 1);
    return;
  }
  const open = target.closest('[data-open]');
  if (open) {
    // The drawing itself opens the viewer: the page's own listener takes the click from there.
    frameOf(open)?.querySelector<HTMLElement>('.diagram-view')?.click();
    return;
  }
  const load = target.closest<HTMLElement>('[data-load]');
  if (load) {
    event.preventDefault();
    allowedMedia.add(load.dataset.load!);
    return;
  }
  const toggle = target.closest<HTMLElement>('[data-toggle]');
  if (toggle) {
    blockModes.set(toggle.dataset.toggle!, toggle.dataset.mode as BlockMode);
    return void refocus(`[data-toggle="${toggle.dataset.toggle}"]`);
  }
  const button = target.closest('[data-copy]');
  const code = button?.closest('.code-block')?.querySelector('pre > code');
  if (!button || !code) return;
  // A fence's code ends with the newline before its closing fence; the copy leaves it out.
  const text = (code.textContent ?? '').replace(/\n$/, '');
  try {
    await navigator.clipboard.writeText(text);
  } catch {
    emit('copyFailed');
    return;
  }
  showCopied(button);
  emit('copied', text);
}

/** An image that does not load shows what it was meant to be (its description) instead of a broken icon. */
function onError(event: Event) {
  const target = event.target as Element;
  if (target instanceof HTMLImageElement) target.closest('.media-view')?.classList.add('broken');
}
</script>

<template>
  <!-- markdown-it's output, with raw HTML switched off (above): a reply's own tags are shown as text. -->
  <div
    ref="root"
    class="markdown"
    @click="onClick"
    @error.capture="onError"
    @wheel="onWheel"
    @scroll.capture="onScroll"
    @pointerdown="onPointerDown"
    @pointermove="onPointerMove"
    @pointerup="onPointerUp"
    @pointercancel="onPointerUp"
  >
    <div v-for="(block, i) in blocks" :key="i" class="md-block" v-html="block" />
  </div>
</template>

<style scoped>
.markdown { min-width: 0; overflow-wrap: anywhere; }
.md-block:first-child :deep(> :first-child) { margin-top: 0; }
.md-block:last-child :deep(> :last-child) { margin-bottom: 0; }
.markdown :deep(p), .markdown :deep(ul), .markdown :deep(ol), .markdown :deep(blockquote), .markdown :deep(.code-block), .markdown :deep(.table), .markdown :deep(hr) { margin: 0 0 var(--app-space-2); }
.markdown :deep(ul), .markdown :deep(ol) { padding-inline-start: var(--app-space-6); }
.markdown :deep(li + li), .markdown :deep(li > ul), .markdown :deep(li > ol) { margin-top: var(--app-space-1); }
.markdown :deep(li > ul), .markdown :deep(li > ol) { margin-bottom: 0; }
.markdown :deep(h1), .markdown :deep(h2), .markdown :deep(h3), .markdown :deep(h4), .markdown :deep(h5), .markdown :deep(h6) {
  margin: var(--app-space-3) 0 var(--app-space-2); font-size: var(--md-sys-typescale-title-medium-size); line-height: var(--md-sys-typescale-title-medium-line-height); font-weight: 600;
}
.markdown :deep(h1) { font-size: var(--md-sys-typescale-title-large-size); line-height: var(--md-sys-typescale-title-large-line-height); }
.markdown :deep(a) { color: var(--md-sys-color-primary); text-decoration: underline; text-underline-offset: 2px; }
.markdown :deep(:not(pre) > code) {
  padding: 1px 4px; border-radius: var(--md-sys-shape-corner-extra-small); background: var(--md-sys-color-surface-container-highest);
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 0.9em;
}
.markdown :deep(blockquote) { padding-inline-start: var(--app-space-3); border-inline-start: 3px solid var(--md-sys-color-outline-variant); color: var(--md-sys-color-on-surface-variant); }
.markdown :deep(hr) { height: 1px; border: 0; background: var(--md-sys-color-outline-variant); }
.markdown :deep(table) { border-collapse: collapse; font-size: var(--md-sys-typescale-body-small-size); }
.markdown :deep(th), .markdown :deep(td) { padding: var(--app-space-1) var(--app-space-2); border: 1px solid var(--md-sys-color-outline-variant); text-align: start; overflow-wrap: normal; }
.markdown :deep(th) { background: var(--md-sys-color-surface-container-high); font-weight: 600; }
/* Math: a formula set apart scrolls sideways on its own when it is wider than the column. */
.markdown :deep(.math-block) { margin: 0 0 var(--app-space-2); padding: var(--app-space-1) 0; --app-scroll-inset: var(--app-space-2); }
.markdown :deep(.math-block .katex-display) { margin: 0; }
.markdown :deep(.katex) { font-size: 1.1em; }
.markdown :deep(.math-error) { color: var(--md-sys-color-error); }
/* SVG drawn in the block, on the block's own surface; a press opens it in the viewer. */
.markdown :deep(.svg-figure) { padding: var(--app-space-2) var(--app-space-3) var(--app-space-3); }
.markdown :deep(.diagram.pending) { padding: var(--app-space-2) var(--app-space-3) var(--app-space-3); color: var(--md-sys-color-on-surface-variant); font-size: var(--md-sys-typescale-body-small-size); }
.markdown :deep(.diagram-view), .markdown :deep(.media-view) {
  display: inline-block; max-width: 100%; padding: 0; border: 0; border-radius: var(--md-sys-shape-corner-small); background: none; color: inherit; cursor: zoom-in;
}
/*
 * A drawn diagram: laid out at its scale (the component sets its size and its frame's height) and
 * centred in its frame while it is smaller; larger, it is clipped to the frame and dragged about in it,
 * or scrolled sideways. A finger on it scrolls the page (or the frame sideways) until two of them pinch
 * it, or the reader zoomed it.
 */
.markdown :deep(.diagram-frame) { padding: var(--app-space-2) var(--app-space-3) var(--app-space-3); }
.markdown :deep(.diagram-frame .diagram) { display: flex; touch-action: pan-x pan-y; --app-scroll-inset: var(--app-space-1); }
.markdown :deep(.diagram-frame .diagram-view) { flex: none; display: block; max-width: none; margin: auto; }
.markdown :deep(.diagram-view svg) { display: block; max-width: none; }
.markdown :deep(.diagram-frame.pannable .diagram-view) { cursor: grab; }
.markdown :deep(.diagram-frame.dragging .diagram-view) { cursor: grabbing; }
.markdown :deep(.diagram-frame.zoomed .diagram) { touch-action: none; }
.markdown :deep(.diagram-error) { margin: 0; padding: var(--app-space-2) var(--app-space-3); color: var(--md-sys-color-error); font-size: var(--md-sys-typescale-body-small-size); }
.markdown :deep(.html-preview) {
  display: block; width: 100%; height: 200px; border: 0; border-radius: 0 0 var(--md-sys-shape-corner-small) var(--md-sys-shape-corner-small);
  background: var(--md-sys-color-surface-container-lowest);
}
/* Images, video and audio: never wider than the column, nor taller than most of a screen. */
.markdown :deep(.media-view img), .markdown :deep(.svg-figure img) { display: block; max-width: 100%; max-height: 480px; height: auto; border-radius: inherit; }
.markdown :deep(.media-view:focus-visible), .markdown :deep(.diagram-view:focus-visible) { outline: 2px solid var(--md-sys-color-primary); outline-offset: 2px; }
.markdown :deep(.media-view.broken) {
  padding: var(--app-space-2) var(--app-space-3); border: 1px dashed var(--md-sys-color-outline-variant); color: var(--md-sys-color-on-surface-variant); cursor: default;
}
.markdown :deep(.media-view.broken img) { display: none; }
.markdown :deep(.media-view.broken::after) { content: attr(data-alt); }
.markdown :deep(.media-video) { display: block; max-width: 100%; max-height: 480px; border-radius: var(--md-sys-shape-corner-small); background: var(--md-sys-color-surface-container-highest); }
.markdown :deep(.media-audio) { display: block; max-width: 100%; }
/* Media on another site, not loaded yet: a button that names the site. */
.markdown :deep(.media-gate) {
  display: inline-flex; align-items: center; gap: var(--app-space-2); max-width: 100%; min-height: 40px; padding: var(--app-space-2) var(--app-space-3);
  border: 1px dashed var(--md-sys-color-outline); border-radius: var(--md-sys-shape-corner-small); background: var(--md-sys-color-surface-container);
  color: var(--md-sys-color-on-surface-variant); font: inherit; font-size: var(--md-sys-typescale-body-medium-size); text-align: start; cursor: pointer;
}
.markdown :deep(.media-gate span) { min-width: 0; overflow-wrap: anywhere; }
.markdown :deep(.media-gate svg) { flex: none; }
.markdown :deep(.media-gate:hover) { background: var(--md-sys-color-surface-container-high); }
</style>
