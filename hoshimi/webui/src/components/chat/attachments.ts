/**
 * Turning what is attached in the Playground into what is uploaded: an image as an image, small
 * enough to send again with every turn; a text file, or a PDF read here in the browser, as its text.
 * Anything else is refused with a reason, before anything is sent.
 */

export const IMAGE_TYPES = ['image/png', 'image/jpeg', 'image/webp', 'image/gif'];
/** The longest side an image is sent at; larger ones are scaled down. */
export const IMAGE_EDGE = 2048;
/** An image under this many bytes, and no larger than ``IMAGE_EDGE``, is sent as it is. */
const IMAGE_KEEP_BYTES = 1_000_000;
/** The most of a text file's (or a PDF's) text that is uploaded. */
export const TEXT_BYTES = 2_000_000;

const TEXT_EXTENSIONS = new Set([
  'txt', 'md', 'markdown', 'csv', 'tsv', 'json', 'jsonl', 'yaml', 'yml', 'toml', 'ini', 'cfg', 'conf', 'env', 'log', 'xml', 'html', 'htm', 'css', 'scss',
  'js', 'mjs', 'cjs', 'ts', 'tsx', 'jsx', 'vue', 'svelte', 'py', 'rb', 'go', 'rs', 'java', 'kt', 'kts', 'swift', 'c', 'h', 'cc', 'cpp', 'hpp', 'cs',
  'php', 'lua', 'sh', 'bash', 'zsh', 'fish', 'ps1', 'bat', 'sql', 'r', 'dart', 'scala', 'pl', 'hs', 'ex', 'exs', 'erl', 'clj', 'tex', 'rst', 'adoc',
  'diff', 'patch', 'gitignore', 'dockerfile', 'makefile', 'gradle', 'properties', 'proto', 'graphql', 'svg',
]);

export type AttachmentKind = 'image' | 'file';

/** A file as it will be uploaded. */
export interface Prepared {
  kind: AttachmentKind;
  name: string;
  blob: Blob;
  /** For a file sent as its text: the file's own type, shown and kept with it. */
  type?: string;
  width?: number;
  height?: number;
}

/** Why a file cannot go: ``unsupported`` (nothing to read in it), ``too-large``, ``no-text`` (a PDF of scans), ``unreadable``. */
export class AttachmentError extends Error {
  constructor(readonly reason: 'unsupported' | 'too-large' | 'no-text' | 'unreadable', readonly fileName: string) {
    super(`${fileName}: ${reason}`);
  }
}

const extension = (name: string) => {
  const base = name.toLowerCase().split('/').pop() ?? '';
  return base.includes('.') ? base.slice(base.lastIndexOf('.') + 1) : base;
};

export function kindOf(file: File): 'image' | 'text' | 'pdf' | null {
  if (IMAGE_TYPES.includes(file.type)) return 'image';
  if (file.type === 'application/pdf' || extension(file.name) === 'pdf') return 'pdf';
  if (file.type.startsWith('text/') || ['application/json', 'application/xml', 'application/x-yaml', 'application/yaml', 'application/javascript', 'application/x-sh', 'application/sql', 'application/toml'].includes(file.type)) return 'text';
  if (TEXT_EXTENSIONS.has(extension(file.name))) return 'text';
  return null;
}

export async function prepare(file: File): Promise<Prepared> {
  const kind = kindOf(file);
  if (kind === 'image') return prepareImage(file);
  if (kind === 'text') {
    const text = await file.text();
    if (text.length > TEXT_BYTES) throw new AttachmentError('too-large', file.name);
    return { kind: 'file', name: file.name, blob: new Blob([text], { type: 'text/plain;charset=utf-8' }), type: file.type || 'text/plain' };
  }
  if (kind === 'pdf') {
    const text = await pdfText(file);
    if (!text.trim()) throw new AttachmentError('no-text', file.name);
    if (text.length > TEXT_BYTES) throw new AttachmentError('too-large', file.name);
    return { kind: 'file', name: file.name, blob: new Blob([text], { type: 'text/plain;charset=utf-8' }), type: 'application/pdf' };
  }
  throw new AttachmentError('unsupported', file.name);
}

/** An image no larger than ``IMAGE_EDGE`` on its longest side, re-encoded only when it has to be. */
async function prepareImage(file: File): Promise<Prepared> {
  let bitmap: ImageBitmap;
  try {
    bitmap = await createImageBitmap(file);
  } catch {
    throw new AttachmentError('unreadable', file.name);
  }
  const { width, height } = bitmap;
  const scale = Math.min(1, IMAGE_EDGE / Math.max(width, height));
  // A GIF would lose its motion; a small image loses nothing by staying as it is.
  if (scale === 1 && (file.size <= IMAGE_KEEP_BYTES || file.type === 'image/gif')) {
    bitmap.close();
    return { kind: 'image', name: file.name, blob: file, width, height };
  }
  const w = Math.round(width * scale);
  const h = Math.round(height * scale);
  const canvas = document.createElement('canvas');
  canvas.width = w;
  canvas.height = h;
  canvas.getContext('2d')?.drawImage(bitmap, 0, 0, w, h);
  bitmap.close();
  const blob = (await toBlob(canvas, 'image/webp')) ?? (await toBlob(canvas, 'image/jpeg'));
  if (!blob) throw new AttachmentError('unreadable', file.name);
  const name = file.name.replace(/\.[^.]+$/, '') + (blob.type === 'image/webp' ? '.webp' : '.jpg');
  return { kind: 'image', name, blob, width: w, height: h };
}

const toBlob = (canvas: HTMLCanvasElement, type: string) =>
  new Promise<Blob | null>((resolve) => canvas.toBlob((blob) => resolve(blob && blob.type === type ? blob : null), type, 0.85));

/** A PDF's text, page by page; pdf.js loads only when a PDF is attached. */
async function pdfText(file: File): Promise<string> {
  const [pdfjs, worker] = await Promise.all([import('pdfjs-dist'), import('pdfjs-dist/build/pdf.worker.min.mjs?url')]);
  pdfjs.GlobalWorkerOptions.workerSrc = worker.default;
  let document;
  try {
    document = await pdfjs.getDocument({ data: new Uint8Array(await file.arrayBuffer()) }).promise;
  } catch {
    throw new AttachmentError('unreadable', file.name);
  }
  const pages: string[] = [];
  try {
    for (let n = 1; n <= document.numPages; n++) {
      const page = await document.getPage(n);
      const content = await page.getTextContent();
      const text = content.items.map((item) => ('str' in item ? item.str + (item.hasEOL ? '\n' : '') : '')).join('');
      pages.push(`--- Page ${n} ---\n${text.trim()}`);
    }
  } finally {
    void document.destroy();
  }
  return pages.join('\n\n');
}

/** "12 KB", "3.4 MB": a size as a reader thinks of it. */
export function size(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}
