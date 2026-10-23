import { computed, inject, reactive, ref, type ComputedRef, type InjectionKey } from 'vue';

/**
 * What ``Markdown`` needs beyond the text, shared by every reply on the page: how far it may reach
 * for media on other sites, the words it shows (a page provides them translated), and the state a
 * reply keeps across its re-renders as it streams (what was let load, which blocks show their code,
 * how tall a preview turned out). Nothing here loads a heavy library.
 */

/** ``ask``: media from another site waits behind a button that names the site; ``always``: it loads at once. */
export type RemoteMedia = 'ask' | 'always';

export interface MarkdownLabels {
  openImage: string;
  /** ``{host}`` is the site the image, video or audio comes from. */
  loadImage: string;
  loadVideo: string;
  loadAudio: string;
  code: string;
  diagram: string;
  image: string;
  preview: string;
  drawing: string;
  /** ``{reason}`` is the diagram library's own message. */
  diagramFailed: string;
  previewTitle: string;
}

export const DEFAULT_LABELS: MarkdownLabels = {
  openImage: 'Open image',
  loadImage: 'Load image from {host}',
  loadVideo: 'Load video from {host}',
  loadAudio: 'Load audio from {host}',
  code: 'Code',
  diagram: 'Diagram',
  image: 'Image',
  preview: 'Preview',
  drawing: 'Drawing the diagram…',
  diagramFailed: 'Couldn’t draw this diagram: {reason}',
  previewTitle: 'HTML preview',
};

export interface MarkdownContext {
  remoteMedia: () => RemoteMedia;
  labels: () => MarkdownLabels;
}
export const MARKDOWN_CONTEXT: InjectionKey<MarkdownContext> = Symbol('markdown');

export function useMarkdownContext(): { remoteMedia: ComputedRef<RemoteMedia>; labels: ComputedRef<MarkdownLabels> } {
  const context = inject(MARKDOWN_CONTEXT, null);
  return {
    remoteMedia: computed(() => context?.remoteMedia() ?? 'ask'),
    labels: computed(() => context?.labels() ?? DEFAULT_LABELS),
  };
}

export const fill = (template: string, values: Record<string, string>) => template.replace(/\{(\w+)\}/g, (all, name: string) => values[name] ?? all);

/** A short, stable name for a piece of text (FNV-1a): the key a block's state and drawing are kept under. */
export function hash(text: string): string {
  let h = 0x811c9dc5;
  for (let i = 0; i < text.length; i++) {
    h ^= text.charCodeAt(i);
    h = Math.imul(h, 0x01000193);
  }
  return `${(h >>> 0).toString(36)}${text.length.toString(36)}`;
}

// -- media ---------------------------------------------------------------------------- #
export type MediaKind = 'image' | 'video' | 'audio';
const VIDEO = /\.(mp4|m4v|webm|mov|ogv)$/i;
const AUDIO = /\.(mp3|m4a|aac|wav|oga|ogg|flac|opus)$/i;

/** What ``![…](src)`` points at, by its type (a ``data:`` address) or its file's extension. */
export function mediaKind(src: string): MediaKind {
  const data = /^data:(image|video|audio)\//i.exec(src);
  if (data) return data[1].toLowerCase() as MediaKind;
  const path = src.split(/[?#]/)[0];
  return VIDEO.test(path) ? 'video' : AUDIO.test(path) ? 'audio' : 'image';
}

/** Media written into the reply itself (``data:``) loads at once; anything else is fetched from somewhere. */
export const isRemote = (src: string) => !/^data:/i.test(src);

export function hostOf(src: string): string {
  try {
    return new URL(src, window.location.href).host || src;
  } catch {
    return src;
  }
}

/**
 * The addresses the reader let load in this tab. An image on another site is a request the model
 * chose: its address can carry what the conversation holds to whoever serves it, so it waits for a
 * press unless the reader chose to always load (Settings › Appearance).
 */
export const allowedMedia = reactive(new Set<string>());

// -- blocks that render ------------------------------------------------------------------ #
/** How a diagram, an SVG or an HTML block shows: drawn (``rendered``) or as its code. */
export type BlockMode = 'rendered' | 'code';
/** The modes the reader chose, by block; a block not here shows its default. */
export const blockModes = reactive(new Map<string, BlockMode>());

/** How tall each HTML preview said it is, so a preview drawn again opens at its height. */
export const frameHeights = new Map<string, number>();
const FRAME_MAX = 720;

/**
 * An HTML block as the document of a sandboxed frame. The frame has scripts but no origin of its
 * own (no ``allow-same-origin``), so it cannot read this app's storage or call its API; the policy
 * written into its head keeps it off the network too, and a small script reports its height.
 */
export function previewDocument(html: string): string {
  const head =
    `<meta charset="utf-8"><meta http-equiv="Content-Security-Policy" content="default-src 'none'; img-src data: blob:; media-src data: blob:; ` +
    `style-src 'unsafe-inline'; font-src data:; script-src 'unsafe-inline'; form-action 'none'">` +
    '<base target="_blank">' +
    '<script>new ResizeObserver(function(){parent.postMessage({hoshimiFrame:document.documentElement.scrollHeight},"*")}).observe(document.documentElement)</script>';
  if (/<head[^>]*>/i.test(html)) return html.replace(/<head[^>]*>/i, (tag) => tag + head);
  if (/<html[^>]*>/i.test(html)) return html.replace(/<html[^>]*>/i, (tag) => `${tag}<head>${head}</head>`);
  return `<!doctype html><html><head>${head}<style>body{margin:8px;font-family:system-ui,sans-serif}</style></head><body>${html}</body></html>`;
}

let listening = false;
/** One listener for every preview on the page: a frame's reported height is applied to that frame. */
export function listenToFrames(): void {
  if (listening || typeof window === 'undefined') return;
  listening = true;
  window.addEventListener('message', (event) => {
    const height = (event.data as { hoshimiFrame?: unknown } | null)?.hoshimiFrame;
    if (typeof height !== 'number' || !Number.isFinite(height)) return;
    for (const frame of document.querySelectorAll<HTMLIFrameElement>('iframe[data-frame]')) {
      if (frame.contentWindow !== event.source) continue;
      const px = Math.max(48, Math.min(FRAME_MAX, Math.ceil(height)));
      frameHeights.set(frame.dataset.frame!, px);
      frame.style.height = `${px}px`;
    }
  });
}

/** Gives each preview the height it last reported, after a re-render put a new frame in its place. */
export function sizeFrames(root: ParentNode): void {
  for (const frame of root.querySelectorAll<HTMLIFrameElement>('iframe[data-frame]')) {
    const px = frameHeights.get(frame.dataset.frame!);
    if (px) frame.style.height = `${px}px`;
  }
}

// -- the theme a diagram is drawn in --------------------------------------------------------- #
/**
 * Changes whenever the theme does (light or dark, the seed colour, the contrast): a diagram is kept
 * under its source and this, so a new theme draws it again in the new colours.
 */
export const themeKey = ref('');
let observing = false;
export function watchThemeKey(): void {
  if (observing || typeof document === 'undefined') return;
  observing = true;
  const root = document.documentElement;
  const read = () => (themeKey.value = `${root.dataset.theme ?? ''}:${root.style.getPropertyValue('--md-sys-color-primary')}:${root.style.getPropertyValue('--md-sys-color-surface')}`);
  read();
  if (typeof MutationObserver !== 'undefined') new MutationObserver(read).observe(root, { attributes: true, attributeFilter: ['style', 'data-theme'] });
}
