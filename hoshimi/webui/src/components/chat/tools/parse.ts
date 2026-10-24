/**
 * What the built-in tools were asked and said, read back into parts a view can lay out. Each reader
 * follows the words the tool writes (``hoshimi/core/agent``: ``files.py``, ``patch.py``,
 * ``terminals.py``, ``web.py``) and gives up (null) on text that is not in that shape, so a view
 * shows anything else as it came: an error, a reply kept from an older version, a tool's new words.
 */

// -- arguments ----------------------------------------------------------------------------- #
/** The call's arguments as an object; none while they are still coming in, or when they are not one. */
export function parseArguments(raw: string): Record<string, unknown> {
  try {
    const value = JSON.parse(raw || '{}');
    return value && typeof value === 'object' && !Array.isArray(value) ? value : {};
  } catch {
    return {};
  }
}

/**
 * A string argument from arguments still coming in (``{"input": "*** Begin Patch\n*** Add``): as much
 * of it as has come, so a patch or a command shows while the model writes it. '' when the key has
 * not come yet.
 */
export function partialString(raw: string, key: string): string {
  const start = new RegExp(String.raw`"${key}"\s*:\s*"`).exec(raw);
  if (!start) return '';
  let out = '';
  for (let i = start.index + start[0].length; i < raw.length; i++) {
    const c = raw[i];
    if (c === '"') return out;
    if (c !== '\\') {
      out += c;
      continue;
    }
    const next = raw[++i];
    if (next === undefined) break;
    if (next === 'u') {
      const hex = raw.slice(i + 1, i + 5);
      if (!/^[\da-f]{4}$/i.test(hex)) break;
      out += String.fromCharCode(parseInt(hex, 16));
      i += 4;
    } else out += ({ n: '\n', t: '\t', r: '\r', b: '\b', f: '\f' } as Record<string, string>)[next] ?? next;
  }
  return out;
}

// -- read ---------------------------------------------------------------------------------- #
export interface FileLine {
  n: number;
  text: string;
  /** Characters of a long line the tool left out. */
  cut: number;
}
export type ReadResult =
  | { kind: 'file'; lines: FileLine[]; total: number | null; next: number | null }
  | { kind: 'dir'; path: string; entries: { name: string; dir: boolean }[]; more: number }
  | { kind: 'empty'; path: string };

const LINE_LIMIT = 500;

/** A ``read``'s output: numbered lines (``L12: text``), a listing (``Directory …:``), or an empty file. */
export function parseRead(output: string): ReadResult | null {
  const dir = /^Directory (.+):\n([\s\S]*)$/.exec(output);
  if (dir) {
    const lines = dir[2].split('\n');
    const more = /^\[(\d+) more entries not shown\.\]$/.exec(lines[lines.length - 1] ?? '');
    if (more) lines.pop();
    const entries = lines[0] === '(empty)' && lines.length === 1 ? [] : lines.filter(Boolean).map((name) => ({ name: name.replace(/\/$/, ''), dir: name.endsWith('/') }));
    return { kind: 'dir', path: dir[1], entries, more: more ? Number(more[1]) : 0 };
  }
  const empty = /^(.+) is empty\.$/.exec(output);
  if (empty && !output.includes('\n')) return { kind: 'empty', path: empty[1] };
  const rows = output.split('\n');
  const trailer = /^\[Lines (\d+)–(\d+) of (\d+)\. Read on with offset=(\d+)\.\]$/.exec(rows[rows.length - 1] ?? '');
  if (trailer) rows.pop();
  const lines: FileLine[] = [];
  for (const row of rows) {
    const m = /^L(\d+): ?(.*)$/.exec(row);
    if (!m) return null;
    const cut = /… \[(\d+) more characters\]$/.exec(m[2]);
    const kept = cut ? m[2].slice(0, cut.index) : '';
    lines.push(cut && kept.length === LINE_LIMIT ? { n: Number(m[1]), text: kept, cut: Number(cut[1]) } : { n: Number(m[1]), text: m[2], cut: 0 });
  }
  if (!lines.length) return null;
  return { kind: 'file', lines, total: trailer ? Number(trailer[3]) : null, next: trailer ? Number(trailer[4]) : null };
}

// -- apply_patch --------------------------------------------------------------------------- #
export type PatchOp = 'add' | 'update' | 'delete';
export interface DiffLine {
  kind: 'context' | 'add' | 'remove';
  text: string;
  /** Its line in the file before (context, removed) and after (context, added), when known. */
  old?: number;
  new?: number;
}
export interface PatchHunk {
  /** The line the hunk follows (``@@ def greet():``), '' for none. */
  header: string;
  lines: DiffLine[];
}
export interface PatchFile {
  op: PatchOp;
  path: string;
  moveTo: string;
  hunks: PatchHunk[];
  added: number;
  removed: number;
}

/**
 * The files a patch changes and how, as the tool reads it (a hunk at each ``@@``, the lines before the
 * first one a hunk of their own), from a patch whole or still being written.
 */
export function parsePatch(input: string): PatchFile[] {
  const files: PatchFile[] = [];
  let file: PatchFile | null = null;
  const hunk = () => {
    if (!file!.hunks.length) file!.hunks.push({ header: '', lines: [] });
    return file!.hunks[file!.hunks.length - 1];
  };
  for (const raw of input.replace(/\r/g, '').split('\n')) {
    const trimmed = raw.trim();
    const header = /^\*\*\* (Add|Update|Delete) File: (.+)$/.exec(trimmed);
    if (header) {
      file = { op: header[1].toLowerCase() as PatchOp, path: header[2], moveTo: '', hunks: [], added: 0, removed: 0 };
      files.push(file);
      continue;
    }
    if (!file || trimmed === '*** End Patch' || trimmed === '*** Begin Patch' || trimmed === '*** End of File') continue;
    if (file.op === 'update' && !file.hunks.length && trimmed.startsWith('*** Move to: ')) {
      file.moveTo = trimmed.slice('*** Move to: '.length);
      continue;
    }
    if (file.op === 'update' && (raw.trimEnd() === '@@' || raw.startsWith('@@ '))) {
      file.hunks.push({ header: raw.trimEnd().slice(3), lines: [] });
      continue;
    }
    if (file.op === 'add') {
      if (raw.startsWith('+')) hunk().lines.push({ kind: 'add', text: raw.slice(1) });
      continue;
    }
    if (file.op !== 'update') continue;
    if (raw === '' || raw[0] === ' ') hunk().lines.push({ kind: 'context', text: raw.slice(1) });
    else if (raw[0] === '+') hunk().lines.push({ kind: 'add', text: raw.slice(1) });
    else if (raw[0] === '-') hunk().lines.push({ kind: 'remove', text: raw.slice(1) });
  }
  for (const f of files) {
    const lines = f.hunks.flatMap((h) => h.lines);
    f.added = lines.filter((l) => l.kind === 'add').length;
    f.removed = lines.filter((l) => l.kind === 'remove').length;
    // A trailing blank line of a hunk stands for the file's last line ending, not a line of it.
    for (const h of f.hunks) if (h.lines.length > 1 && h.lines[h.lines.length - 1].kind === 'context' && h.lines[h.lines.length - 1].text === '') h.lines.pop();
  }
  return files;
}

/** What the tool said of a file once it ran (``meta.files``): where it went, its counts, where each hunk landed. */
export interface PatchedFile {
  path: string;
  kind: PatchOp;
  from?: string;
  added?: number;
  removed?: number;
  hunks?: [number, number][];
}
export function patchedFiles(meta: Record<string, unknown>): PatchedFile[] {
  const files = meta.files;
  return Array.isArray(files) ? files.filter((f): f is PatchedFile => !!f && typeof f === 'object' && typeof (f as PatchedFile).path === 'string') : [];
}

/** Numbers each line of a file's hunks: from the lines the tool says they start at, or from 1 for a new file. */
export function numberLines(file: PatchFile, starts?: [number, number][]): PatchFile {
  if (file.op === 'add') {
    let n = 0;
    return { ...file, hunks: file.hunks.map((h) => ({ ...h, lines: h.lines.map((l) => ({ ...l, new: ++n })) })) };
  }
  if (!starts || starts.length !== file.hunks.length) return file;
  return {
    ...file,
    hunks: file.hunks.map((h, i) => {
      let [old, next] = starts[i];
      return {
        ...h,
        lines: h.lines.map((l) => (l.kind === 'context' ? { ...l, old: old++, new: next++ } : l.kind === 'remove' ? { ...l, old: old++ } : { ...l, new: next++ })),
      };
    }),
  };
}

// -- commands ------------------------------------------------------------------------------ #
export interface CommandResult {
  exitCode: number | null;
  /** Seconds, as the tool wrote them. */
  wall: string;
  timedOut: boolean;
  /** A background command still running, and its session. */
  running: boolean;
  sessionId: number | null;
  output: string;
}

/**
 * A command's report: ``shell``'s (``Exit code``, ``Wall time``), a session's (``Process running with
 * session ID``, ``Process exited with code``) or a stopped one's (``Session N stopped (exit code X).``),
 * each with its output after ``Output:``.
 */
export function parseCommand(output: string): CommandResult | null {
  const at = output.indexOf('Output:\n');
  const tail = output.endsWith('Output:') ? output.length - 'Output:'.length : -1;
  const cut = at >= 0 ? at : tail;
  if (cut < 0) return null;
  const result: CommandResult = { exitCode: null, wall: '', timedOut: false, running: false, sessionId: null, output: output.slice(cut + 'Output:\n'.length) };
  for (const line of output.slice(0, cut).split('\n').filter(Boolean)) {
    let m: RegExpExecArray | null;
    if ((m = /^Exit code: (-?\d+)$/.exec(line))) result.exitCode = Number(m[1]);
    else if ((m = /^Wall time: ([\d.]+) seconds$/.exec(line))) result.wall = m[1];
    else if ((m = /^Process running with session ID (\d+)$/.exec(line))) Object.assign(result, { running: true, sessionId: Number(m[1]) });
    else if ((m = /^Process exited with code (-?\d+)$/.exec(line))) result.exitCode = Number(m[1]);
    else if ((m = /^Session (\d+) stopped \(exit code (-?\d+|None)\)\.$/.exec(line))) Object.assign(result, { sessionId: Number(m[1]), exitCode: m[2] === 'None' ? null : Number(m[2]) });
    else return null;
  }
  const note = /^command timed out after \d+ milliseconds\n/.exec(result.output);
  if (note) Object.assign(result, { timedOut: true, output: result.output.slice(note[0].length) });
  return result;
}

/**
 * Terminal output as it would stand on a screen: colour and cursor codes taken out, and a line that
 * was written over (a progress bar's ``\r``) as it was left.
 */
export function terminalText(text: string): string {
  return text
    .replace(/\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)/g, '')
    .replace(/\x1b\[[0-?]*[ -/]*[@-~]/g, '')
    .replace(/\x1b[@-Z\\-_]/g, '')
    .split('\n')
    .map((line) => {
      const parts = line.replace(/\r$/, '').split('\r');
      return parts.filter(Boolean).pop() ?? '';
    })
    .join('\n');
}

/** Keys typed into a session as they read: control characters by their caret names (``^C``), a newline as ⏎. */
export function typedKeys(chars: string): string {
  return [...chars]
    .map((c) => {
      const code = c.charCodeAt(0);
      if (c === '\n' || c === '\r') return '⏎';
      if (c === '\t') return '⇥';
      if (code === 0x1b) return '^[';
      if (code < 0x20) return `^${String.fromCharCode(code + 64)}`;
      if (code === 0x7f) return '^?';
      return c;
    })
    .join('');
}

// -- the web ------------------------------------------------------------------------------- #
export interface SearchResult {
  title: string;
  url: string;
  snippet: string;
}

/** A ``web_search``'s results (``Results for “query”:`` and numbered entries), or none (``No results for``). */
export function parseSearch(output: string): { query: string; results: SearchResult[] } | null {
  const none = /^No results for “([\s\S]*)”\.$/.exec(output);
  if (none) return { query: none[1], results: [] };
  const head = /^Results for “([\s\S]*?)”:\n/.exec(output);
  if (!head) return null;
  const results: SearchResult[] = [];
  for (const block of output.slice(head[0].length).split(/\n(?=\d+\. )/)) {
    const lines = block.replace(/^\n+/, '').split('\n');
    const title = /^\d+\. (.*)$/.exec(lines[0] ?? '');
    if (!title) continue;
    const url = (lines[1] ?? '').trim();
    results.push({ title: title[1], url, snippet: lines.slice(2).map((l) => l.trim()).join(' ').trim() });
  }
  return { query: head[1], results };
}

/** A ``web_fetch``'s page: its address, title and text (Markdown), and where it was cut. */
export function parseFetch(output: string): { url: string; title: string; body: string; cut: { at: number; of: number } | null } | null {
  const m = /^URL: (.*)\n(?:Title: (.*)\n)?\n([\s\S]*)$/.exec(output);
  if (!m) return null;
  const cut = /\n\n\[Cut at (\d+) of (\d+) characters\.\]$/.exec(m[3]);
  return { url: m[1], title: m[2] ?? '', body: cut ? m[3].slice(0, cut.index) : m[3], cut: cut ? { at: Number(cut[1]), of: Number(cut[2]) } : null };
}

/** The address as a reader names it: its host, and its path when there is one. */
export function hostOf(url: string): string {
  try {
    const u = new URL(url.includes('://') ? url : `https://${url}`);
    return u.host + (u.pathname === '/' ? '' : u.pathname);
  } catch {
    return url;
  }
}

/** The output as JSON, pretty, when it is an object or a list; null for anything else. */
export function prettyJson(text: string): string | null {
  const trimmed = text.trim();
  if (!/^[[{]/.test(trimmed)) return null;
  try {
    return JSON.stringify(JSON.parse(trimmed), null, 2);
  } catch {
    return null;
  }
}
