import katex from 'katex';
import 'katex/dist/katex.min.css';
import type MarkdownIt from 'markdown-it';
import type StateBlock from 'markdown-it/lib/rules_block/state_block.mjs';
import type StateInline from 'markdown-it/lib/rules_inline/state_inline.mjs';

/**
 * TeX in a reply, typeset by KaTeX: ``$…$`` and ``\(…\)`` in a line, ``$$…$$`` and ``\[…\]`` on lines
 * of their own (or within a line, set apart). A lone ``$`` stays a dollar: one that opens must be
 * followed by something other than a space, and one that closes must follow something other than a
 * space and not run into a digit, so "$5 and $10" is money. Math still being written (no closing
 * mark yet, as a reply streams) shows as the text it is until it closes. KaTeX is given no trust
 * (no links, no classes, no styles from the source) and a cap on macro expansion; TeX it cannot
 * read shows as code, marked.
 */
const cache = new Map<string, string>();
const CACHE_LIMIT = 500;

export function tex(source: string, display: boolean, escape: (text: string) => string): string {
  const key = `${display ? 'D' : 'I'}${source}`;
  const hit = cache.get(key);
  if (hit !== undefined) return hit;
  let html: string;
  try {
    html = katex.renderToString(source, { displayMode: display, throwOnError: true, trust: false, strict: 'ignore', maxExpand: 1000, maxSize: 50, output: 'htmlAndMathml' });
  } catch (error) {
    const reason = error instanceof Error ? error.message : '';
    html = `<code class="math-error" title="${escape(reason)}">${escape(source)}</code>`;
  }
  if (cache.size >= CACHE_LIMIT) cache.delete(cache.keys().next().value!);
  cache.set(key, html);
  return html;
}

const DELIMITERS: [open: string, close: string, display: boolean][] = [
  ['\\(', '\\)', false],
  ['\\[', '\\]', true],
  ['$$', '$$', true],
];

function closingDollar(src: string, from: number, max: number): number {
  for (let at = src.indexOf('$', from); at >= 0 && at < max; at = src.indexOf('$', at + 1)) {
    if (src[at - 1] === '\\' || /\s/.test(src[at - 1]) || /\d/.test(src[at + 1] ?? '')) continue;
    return at;
  }
  return -1;
}

function inline(state: StateInline, silent: boolean): boolean {
  const { src, pos, posMax } = state;
  let open = '';
  let close = '';
  let display = false;
  for (const [o, c, d] of DELIMITERS) {
    if (src.startsWith(o, pos)) {
      [open, close, display] = [o, c, d];
      break;
    }
  }
  if (!open && src[pos] === '$') [open, close] = ['$', '$'];
  if (!open) return false;
  const start = pos + open.length;
  let end: number;
  if (open === '$') {
    if (!src[start] || /\s/.test(src[start])) return false;
    end = closingDollar(src, start, posMax);
  } else {
    end = src.indexOf(close, start);
    if (end + close.length > posMax) end = -1;
  }
  if (end <= start) return false;
  if (!silent) {
    const token = state.push('math_inline', 'math', 0);
    token.content = src.slice(start, end);
    token.markup = open;
    token.meta = { display };
  }
  state.pos = end + close.length;
  return true;
}

function block(state: StateBlock, startLine: number, endLine: number, silent: boolean): boolean {
  if (state.sCount[startLine] - state.blkIndent >= 4) return false;
  const lineText = (line: number) => state.src.slice(state.bMarks[line] + state.tShift[line], state.eMarks[line]).trimEnd();
  const first = lineText(startLine);
  const delimiter = DELIMITERS.find(([o, , d]) => d && first.startsWith(o));
  if (!delimiter) return false;
  const [open, close] = delimiter;
  const rest = first.slice(open.length);
  let content: string;
  let last = startLine;
  if (rest.includes(close)) {
    // On one line: the whole line, or it is math within a paragraph.
    if (!rest.endsWith(close) || rest.length === close.length) return false;
    content = rest.slice(0, -close.length);
  } else {
    const lines = [rest];
    let closed = false;
    for (let line = startLine + 1; line < endLine; line++) {
      const text = lineText(line);
      if (text.endsWith(close)) {
        lines.push(text.slice(0, -close.length));
        last = line;
        closed = true;
        break;
      }
      lines.push(text);
    }
    if (!closed) return false;
    content = lines.join('\n');
  }
  if (!content.trim()) return false;
  if (silent) return true;
  state.line = last + 1;
  const token = state.push('math_block', 'math', 0);
  token.block = true;
  token.content = content;
  token.markup = open;
  token.map = [startLine, state.line];
  return true;
}

export function mathPlugin(md: MarkdownIt): void {
  const escape = md.utils.escapeHtml;
  // Before ``escape``, which would take ``\(`` and ``\[`` for escaped brackets.
  md.inline.ruler.before('escape', 'math_inline', inline);
  md.block.ruler.before('fence', 'math_block', block, { alt: ['paragraph', 'reference', 'blockquote', 'list'] });
  md.renderer.rules.math_inline = (tokens, i) => tex(tokens[i].content, !!tokens[i].meta?.display, escape);
  md.renderer.rules.math_block = (tokens, i) => `<div class="math-block app-scroll app-scroll-x app-scroll-auto">${tex(tokens[i].content, true, escape)}</div>\n`;
}
