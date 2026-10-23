<script lang="ts">
import MarkdownIt from 'markdown-it';
import type Token from 'markdown-it/lib/token.mjs';
import { highlight } from '@/ui/highlight';
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
 * A code block is highlighted when its fence names a language this build knows (``highlight.ts``)
 * and carries a head with that name and a copy button. The button is plain HTML in the rendered
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
 *   zooms in place (Ctrl or ⌘ with the wheel, a pinch on a trackpad or a screen, or its buttons) and is
 *   dragged about while zoomed; a plain wheel still scrolls the conversation, and a press that did not
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
const IMAGE_ICON = ICON('icon-media', '<rect width="18" height="18" x="3" y="3" rx="2" ry="2"/><circle cx="9" cy="9" r="2"/><path d="m21 15-3.086-3.086a2 2 0 0 0-2.828 0L6 21"/>');
const PLAY_ICON = ICON('icon-media', '<polygon points="6 3 20 12 6 21 6 3"/>');
const ZOOM_ICONS = {
  in: ICON('', '<circle cx="11" cy="11" r="8"/><path d="m21 21-4.35-4.35"/><path d="M11 8v6"/><path d="M8 11h6"/>'),
  out: ICON('', '<circle cx="11" cy="11" r="8"/><path d="m21 21-4.35-4.35"/><path d="M8 11h6"/>'),
  reset: ICON('', '<path d="M3 7V5a2 2 0 0 1 2-2h2"/><path d="M17 3h2a2 2 0 0 1 2 2v2"/><path d="M21 17v2a2 2 0 0 1-2 2h-2"/><path d="M7 21H5a2 2 0 0 1-2-2v-2"/>'),
};
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
  /** What shows above the code (a diagram, an image, a preview). */
  body?: string;
  hideCode?: boolean;
}

function codeBlock(code: string, info: string, env: Env, parts: BlockParts = {}): string {
  const language = info.trim().split(/\s+/)[0] ?? '';
  const highlighted = highlight(code, language);
  const label = escape(env.copyLabel);
  return (
    '<div class="code-block">' +
    `<div class="code-head"><span class="code-lang">${escape(language)}</span><span class="code-tools">${parts.tools ?? ''}` +
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

function diagram(source: string, env: Env): Pick<BlockParts, 'body' | 'hideCode'> {
  const key = hash(`${env.theme}\n${source}`);
  const drawing = drawings.get(key);
  if (!drawing) {
    diagramSources.set(key, source);
    return { body: `<div class="diagram pending" data-mermaid="${key}" role="status">${escape(env.labels.drawing)}</div>`, hideCode: true };
  }
  if (drawing.error !== undefined) return { body: `<p class="diagram-error" role="note">${escape(fill(env.labels.diagramFailed, { reason: drawing.error }))}</p>` };
  const tool = (zoom: keyof typeof ZOOM_ICONS, label: string) =>
    `<button type="button" class="diagram-tool${zoom === 'reset' ? ' diagram-reset' : ''}" data-zoom="${zoom}" aria-label="${escape(label)}" title="${escape(label)}">${ZOOM_ICONS[zoom]}</button>`;
  return {
    body:
      '<div class="diagram-frame">' +
      `<div class="diagram-tools">${tool('out', env.labels.zoomOut)}${tool('in', env.labels.zoomIn)}${tool('reset', env.labels.resetZoom)}</div>` +
      `<div class="diagram app-scroll app-scroll-x app-scroll-auto">${viewButton(svgUrl(drawing.svg!), env.labels.diagram, env, drawing.svg!, 'diagram-view')}</div>` +
      '</div>',
    hideCode: true,
  };
}

function richBlock(kind: Rich, token: Token, env: Env): string {
  const source = token.content;
  const key = hash(`${kind}\n${source}`);
  if (!closed(token, env)) return codeBlock(source, token.info, env);
  const mode: BlockMode = blockModes.get(key) ?? (kind === 'html' ? 'code' : 'rendered');
  const other: BlockMode = mode === 'rendered' ? 'code' : 'rendered';
  const label = mode === 'rendered' ? env.labels.code : { mermaid: env.labels.diagram, svg: env.labels.image, html: env.labels.preview }[kind];
  const tools = `<button type="button" class="code-toggle" data-toggle="${key}" data-mode="${other}">${escape(label)}</button>`;
  if (mode === 'code') return codeBlock(source, token.info, env, { tools });
  if (kind === 'mermaid') return codeBlock(source, token.info, env, { tools, ...diagram(source, env) });
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
import { computed, nextTick, onMounted, onUpdated, ref } from 'vue';
import { listenToFrames, sizeFrames, themeKey, useMarkdownContext, watchThemeKey } from '@/ui/markdownRich';
import { draw } from '@/ui/mermaid';

const props = defineProps<{ source: string; copyLabel: string; copiedLabel: string }>();
const emit = defineEmits<{ copied: [code: string]; copyFailed: [] }>();
const { remoteMedia, labels } = useMarkdownContext();
watchThemeKey();
listenToFrames();
const blocks = computed(() => renderBlocks(props.source, { copyLabel: props.copyLabel, labels: labels.value, remote: remoteMedia.value, theme: themeKey.value }));
const root = ref<HTMLElement | null>(null);

// After each render: draw the diagrams it left a place for, and give previews their height back.
function settle() {
  const el = root.value;
  if (!el) return;
  for (const holder of el.querySelectorAll<HTMLElement>('[data-mermaid]')) {
    const key = holder.dataset.mermaid!;
    const source = diagramSources.get(key);
    if (source !== undefined) draw(key, source);
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

// -- a drawn diagram, zoomed and dragged in place ------------------------------------------------ #
/** How far each diagram is zoomed and moved, by its frame: a frame a render replaces starts afresh. */
interface Zoom {
  scale: number;
  x: number;
  y: number;
}
const zooms = new WeakMap<Element, Zoom>();
const DIAGRAM_MAX = 6;
const zoomOf = (frame: Element): Zoom => zooms.get(frame) ?? (zooms.set(frame, { scale: 1, x: 0, y: 0 }), zooms.get(frame)!);
function paint(frame: HTMLElement, z: Zoom) {
  const svg = frame.querySelector<SVGElement>('.diagram-view svg');
  if (svg) svg.style.transform = z.scale === 1 && !z.x && !z.y ? '' : `translate(${z.x}px, ${z.y}px) scale(${z.scale})`;
  frame.classList.toggle('zoomed', z.scale > 1.01);
}
/** Zoom a diagram to ``scale`` about a point of the window (its centre by default), which stays where it is. */
function zoomDiagram(frame: HTMLElement, scale: number, at?: { x: number; y: number }) {
  const z = zoomOf(frame);
  const next = Math.min(DIAGRAM_MAX, Math.max(1, scale));
  const box = frame.querySelector('.diagram-view svg')?.getBoundingClientRect();
  // The drawing scales about its centre, so its box's centre less the move is where that centre sits unzoomed.
  const px = at && box ? at.x - (box.left + box.width / 2 - z.x) : 0;
  const py = at && box ? at.y - (box.top + box.height / 2 - z.y) : 0;
  const ratio = next / z.scale;
  Object.assign(z, next === 1 ? { scale: 1, x: 0, y: 0 } : { scale: next, x: px - (px - z.x) * ratio, y: py - (py - z.y) * ratio });
  paint(frame, z);
}
const frameAt = (target: EventTarget | null) => (target as Element | null)?.closest?.<HTMLElement>('.diagram-frame') ?? null;

/** With Ctrl or ⌘ (a trackpad's pinch sends that too) the wheel zooms a diagram; alone it scrolls the page. */
function onWheel(event: WheelEvent) {
  const frame = frameAt(event.target);
  if (!frame || !(event.ctrlKey || event.metaKey)) return;
  event.preventDefault();
  const delta = Math.max(-50, Math.min(50, event.deltaY));
  zoomDiagram(frame, zoomOf(frame).scale * Math.exp(-delta * 0.006), { x: event.clientX, y: event.clientY });
}

// Pointers on a diagram: two pinch it; one drags it while it is zoomed. A press that moved is not a click.
const pointers = new Map<number, { x: number; y: number }>();
let gesture: { frame: HTMLElement; start: { x: number; y: number }; moved: boolean; pinch: { distance: number; scale: number } | null } | null = null;
let swallowClick = false;
const spread = () => {
  const [a, b] = [...pointers.values()];
  return Math.hypot(a.x - b.x, a.y - b.y);
};
function onPointerDown(event: PointerEvent) {
  const frame = frameAt(event.target);
  if (!frame || (event.target as Element).closest('.diagram-tools') || (gesture && gesture.frame !== frame)) return;
  pointers.set(event.pointerId, { x: event.clientX, y: event.clientY });
  gesture ??= { frame, start: { x: event.clientX, y: event.clientY }, moved: false, pinch: null };
  if (pointers.size === 2) gesture.pinch = { distance: spread(), scale: zoomOf(frame).scale };
  if (pointers.size === 2 || zoomOf(frame).scale > 1.01) frame.setPointerCapture?.(event.pointerId);
}
function onPointerMove(event: PointerEvent) {
  const last = pointers.get(event.pointerId);
  if (!last || !gesture) return;
  pointers.set(event.pointerId, { x: event.clientX, y: event.clientY });
  if (Math.hypot(event.clientX - gesture.start.x, event.clientY - gesture.start.y) > 4) gesture.moved = true;
  const z = zoomOf(gesture.frame);
  if (gesture.pinch && pointers.size === 2) {
    const [a, b] = [...pointers.values()];
    zoomDiagram(gesture.frame, (gesture.pinch.scale * spread()) / gesture.pinch.distance, { x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 });
  } else if (z.scale > 1.01 && pointers.size === 1) {
    z.x += event.clientX - last.x;
    z.y += event.clientY - last.y;
    paint(gesture.frame, z);
  }
}
function onPointerUp(event: PointerEvent) {
  if (!pointers.delete(event.pointerId) || !gesture) return;
  if (pointers.size < 2) gesture.pinch = null;
  if (pointers.size) return;
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
  const frame = frameAt(zoom);
  if (zoom && frame) {
    const scale = zoomOf(frame).scale;
    zoomDiagram(frame, zoom.dataset.zoom === 'in' ? scale * 1.5 : zoom.dataset.zoom === 'out' ? scale / 1.5 : 1);
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
.markdown :deep(code) {
  padding: 1px 4px; border-radius: var(--md-sys-shape-corner-extra-small); background: var(--md-sys-color-surface-container-highest);
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 0.9em;
}
.markdown :deep(.code-block) { border-radius: var(--md-sys-shape-corner-small); background: var(--md-sys-color-surface-container-highest); }
.markdown :deep(.code-head) {
  display: flex; align-items: center; justify-content: space-between; gap: var(--app-space-2); padding: var(--app-space-1) var(--app-space-1) 0 var(--app-space-3);
  color: var(--md-sys-color-on-surface-variant); font-size: var(--md-sys-typescale-label-small-size); line-height: var(--md-sys-typescale-label-small-line-height);
}
.markdown :deep(.code-lang) { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }
.markdown :deep(.code-copy) {
  display: grid; place-items: center; width: 32px; height: 32px; padding: 0; border: 0; border-radius: var(--md-sys-shape-corner-full);
  background: transparent; color: inherit; cursor: pointer;
  transition: background-color var(--md-sys-motion-duration-short2) var(--md-sys-motion-easing-standard);
}
.markdown :deep(.code-copy:hover) { background: color-mix(in srgb, currentColor calc(var(--md-sys-state-hover-state-layer-opacity) * 100%), transparent); }
.markdown :deep(.code-copy .icon-check), .markdown :deep(.code-copy.copied .icon-copy) { display: none; }
.markdown :deep(.code-copy.copied .icon-check) { display: block; }
.markdown :deep(.code-copy.copied) { color: var(--md-sys-color-primary); }
.markdown :deep(.code-copy:active) { background: color-mix(in srgb, currentColor calc(var(--md-sys-state-pressed-state-layer-opacity) * 100%), transparent); }
.markdown :deep(pre) { margin: 0; padding: 0 var(--app-space-3) var(--app-space-2); white-space: pre; overflow-wrap: normal; --app-scroll-inset: var(--app-space-2); }
.markdown :deep(pre code) { padding: 0; background: none; font-size: var(--md-sys-typescale-body-small-size); line-height: 1.5; }
/* Highlighted code in the theme's own roles, so it follows the seed colour and the dark theme. */
.markdown :deep(.hljs-comment), .markdown :deep(.hljs-quote), .markdown :deep(.hljs-meta) { color: var(--md-sys-color-on-surface-variant); }
.markdown :deep(.hljs-comment), .markdown :deep(.hljs-quote) { font-style: italic; }
.markdown :deep(.hljs-keyword), .markdown :deep(.hljs-literal), .markdown :deep(.hljs-selector-tag), .markdown :deep(.hljs-section), .markdown :deep(.hljs-name), .markdown :deep(.hljs-doctag) { color: var(--md-sys-color-primary); }
.markdown :deep(.hljs-string), .markdown :deep(.hljs-regexp), .markdown :deep(.hljs-addition), .markdown :deep(.hljs-char.escape_), .markdown :deep(.hljs-meta .hljs-string) { color: var(--md-sys-color-tertiary); }
.markdown :deep(.hljs-number), .markdown :deep(.hljs-symbol), .markdown :deep(.hljs-bullet), .markdown :deep(.hljs-attr), .markdown :deep(.hljs-attribute), .markdown :deep(.hljs-variable), .markdown :deep(.hljs-template-variable), .markdown :deep(.hljs-type), .markdown :deep(.hljs-selector-class), .markdown :deep(.hljs-selector-id) { color: var(--md-sys-color-secondary); }
.markdown :deep(.hljs-title), .markdown :deep(.hljs-built_in), .markdown :deep(.hljs-strong) { font-weight: 600; }
.markdown :deep(.hljs-deletion) { color: var(--md-sys-color-error); }
.markdown :deep(.hljs-emphasis) { font-style: italic; }
.markdown :deep(blockquote) { padding-inline-start: var(--app-space-3); border-inline-start: 3px solid var(--md-sys-color-outline-variant); color: var(--md-sys-color-on-surface-variant); }
.markdown :deep(hr) { height: 1px; border: 0; background: var(--md-sys-color-outline-variant); }
.markdown :deep(table) { border-collapse: collapse; font-size: var(--md-sys-typescale-body-small-size); }
.markdown :deep(th), .markdown :deep(td) { padding: var(--app-space-1) var(--app-space-2); border: 1px solid var(--md-sys-color-outline-variant); text-align: start; overflow-wrap: normal; }
.markdown :deep(th) { background: var(--md-sys-color-surface-container-high); font-weight: 600; }
/* The head's buttons: a switch between a block's drawing and its code, then copy. */
.markdown :deep(.code-tools) { display: inline-flex; align-items: center; gap: var(--app-space-1); }
.markdown :deep(.code-toggle) {
  height: 28px; padding: 0 var(--app-space-2); border: 0; border-radius: var(--md-sys-shape-corner-full); background: transparent; color: inherit;
  font: inherit; cursor: pointer;
}
.markdown :deep(.code-toggle:hover) { background: color-mix(in srgb, currentColor calc(var(--md-sys-state-hover-state-layer-opacity) * 100%), transparent); }
.markdown :deep(.code-toggle:active) { background: color-mix(in srgb, currentColor calc(var(--md-sys-state-pressed-state-layer-opacity) * 100%), transparent); }
/* Math: a formula set apart scrolls sideways on its own when it is wider than the column. */
.markdown :deep(.math-block) { margin: 0 0 var(--app-space-2); padding: var(--app-space-1) 0; --app-scroll-inset: var(--app-space-2); }
.markdown :deep(.math-block .katex-display) { margin: 0; }
.markdown :deep(.katex) { font-size: 1.1em; }
.markdown :deep(.math-error) { color: var(--md-sys-color-error); }
/* Diagrams and SVG drawn in the block, on the block's own surface; a press opens them in the viewer. */
.markdown :deep(.diagram), .markdown :deep(.svg-figure) { padding: var(--app-space-2) var(--app-space-3) var(--app-space-3); --app-scroll-inset: var(--app-space-2); }
.markdown :deep(.diagram.pending) { color: var(--md-sys-color-on-surface-variant); font-size: var(--md-sys-typescale-body-small-size); }
.markdown :deep(.diagram-view), .markdown :deep(.media-view) {
  display: inline-block; max-width: 100%; padding: 0; border: 0; border-radius: var(--md-sys-shape-corner-small); background: none; color: inherit; cursor: zoom-in;
}
/* The drawing takes the column's width (Mermaid sizes it as a share of its box), up to its own. */
.markdown :deep(.diagram-view) { display: block; width: 100%; margin: 0 auto; }
.markdown :deep(.diagram-view svg) { display: block; height: auto; margin: 0 auto; transform-origin: center; }
/* Zoomed in place: the drawing is clipped to its box (not scrolled) and dragged about; a finger on it
   scrolls the page until two of them pinch it, or it is zoomed. */
.markdown :deep(.diagram-frame) { position: relative; }
.markdown :deep(.diagram-frame .diagram) { touch-action: pan-x pan-y; }
.markdown :deep(.diagram-frame.zoomed .diagram) { overflow: hidden; touch-action: none; }
.markdown :deep(.diagram-frame.zoomed .diagram-view) { cursor: grab; }
.markdown :deep(.diagram-frame.zoomed .diagram-view:active) { cursor: grabbing; }
.markdown :deep(.diagram-tools) {
  position: absolute; top: var(--app-space-2); right: var(--app-space-2); z-index: 1; display: flex; gap: 2px; padding: 2px;
  border-radius: var(--md-sys-shape-corner-full); background: var(--md-sys-color-surface-container-high); box-shadow: var(--app-elevation-1);
  opacity: 0; transition: opacity var(--md-sys-motion-duration-short3) var(--md-sys-motion-easing-standard);
}
.markdown :deep(.diagram-frame:hover .diagram-tools), .markdown :deep(.diagram-tools:focus-within), .markdown :deep(.diagram-frame.zoomed .diagram-tools) { opacity: 1; }
@media (hover: none) { .markdown :deep(.diagram-tools) { opacity: 1; } }
.markdown :deep(.diagram-tool) {
  display: grid; place-items: center; width: 32px; height: 32px; padding: 0; border: 0; border-radius: var(--md-sys-shape-corner-full);
  background: transparent; color: var(--md-sys-color-on-surface-variant); cursor: pointer;
}
.markdown :deep(.diagram-tool:hover) { background: color-mix(in srgb, currentColor calc(var(--md-sys-state-hover-state-layer-opacity) * 100%), transparent); }
.markdown :deep(.diagram-tool:active) { background: color-mix(in srgb, currentColor calc(var(--md-sys-state-pressed-state-layer-opacity) * 100%), transparent); }
.markdown :deep(.diagram-frame:not(.zoomed) .diagram-reset) { display: none; }
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
