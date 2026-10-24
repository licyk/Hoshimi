import hljs from 'highlight.js/lib/core';
import bash from 'highlight.js/lib/languages/bash';
import c from 'highlight.js/lib/languages/c';
import cpp from 'highlight.js/lib/languages/cpp';
import csharp from 'highlight.js/lib/languages/csharp';
import css from 'highlight.js/lib/languages/css';
import diff from 'highlight.js/lib/languages/diff';
import dockerfile from 'highlight.js/lib/languages/dockerfile';
import go from 'highlight.js/lib/languages/go';
import ini from 'highlight.js/lib/languages/ini';
import java from 'highlight.js/lib/languages/java';
import javascript from 'highlight.js/lib/languages/javascript';
import json from 'highlight.js/lib/languages/json';
import kotlin from 'highlight.js/lib/languages/kotlin';
import lua from 'highlight.js/lib/languages/lua';
import markdown from 'highlight.js/lib/languages/markdown';
import php from 'highlight.js/lib/languages/php';
import powershell from 'highlight.js/lib/languages/powershell';
import python from 'highlight.js/lib/languages/python';
import ruby from 'highlight.js/lib/languages/ruby';
import rust from 'highlight.js/lib/languages/rust';
import scss from 'highlight.js/lib/languages/scss';
import shell from 'highlight.js/lib/languages/shell';
import sql from 'highlight.js/lib/languages/sql';
import swift from 'highlight.js/lib/languages/swift';
import typescript from 'highlight.js/lib/languages/typescript';
import xml from 'highlight.js/lib/languages/xml';
import yaml from 'highlight.js/lib/languages/yaml';

/**
 * Syntax highlighting for the code blocks in a reply: highlight.js's core with the languages models
 * write most, rather than all of them (the whole set would triple the Playground's script). Each
 * language brings its usual aliases (``js``, ``ts``, ``py``, ``sh``, ``yml``, ``html``, ``toml``…).
 */
const LANGUAGES = {
  bash, c, cpp, csharp, css, diff, dockerfile, go, ini, java, javascript, json, kotlin, lua, markdown,
  php, powershell, python, ruby, rust, scss, shell, sql, swift, typescript, xml, yaml,
};
for (const [name, language] of Object.entries(LANGUAGES)) hljs.registerLanguage(name, language);

/**
 * ``code`` as highlighted HTML (spans with ``hljs-*`` classes around escaped text), or null for a
 * language this build does not know; the caller then shows the code as plain text. An unnamed
 * block is never guessed at: a guess is slow and, on a short snippet, often wrong.
 */
export function highlight(code: string, language: string): string | null {
  if (!language || !hljs.getLanguage(language)) return null;
  return hljs.highlight(code, { language, ignoreIllegals: true }).value;
}

/** Names a fence's language word shows as where highlight.js's own is a list (``HTML, XML``) or not a name. */
const NAMES: Record<string, string> = {
  html: 'HTML', htm: 'HTML', xhtml: 'XHTML', xml: 'XML', svg: 'SVG', vue: 'Vue', shell: 'Shell', console: 'Shell', zsh: 'Zsh',
  toml: 'TOML', ini: 'INI', php: 'PHP', jsx: 'JSX', tsx: 'TSX', mermaid: 'Mermaid', patch: 'Diff',
};

/** What a fence's language is called (``py`` is Python): its own word for one this build does not know, '' for none. */
export function languageName(language: string): string {
  const lower = language.toLowerCase();
  if (!lower) return '';
  return NAMES[lower] ?? hljs.getLanguage(lower)?.name?.split(',')[0] ?? language;
}

const EXTENSIONS: Record<string, string> = {
  ts: 'typescript', mts: 'typescript', cts: 'typescript', tsx: 'typescript', js: 'javascript', mjs: 'javascript', cjs: 'javascript', jsx: 'javascript',
  py: 'python', pyi: 'python', rs: 'rust', go: 'go', java: 'java', kt: 'kotlin', kts: 'kotlin', c: 'c', h: 'c', cc: 'cpp', cpp: 'cpp', cxx: 'cpp',
  hpp: 'cpp', hh: 'cpp', cs: 'csharp', rb: 'ruby', php: 'php', lua: 'lua', swift: 'swift', sh: 'bash', bash: 'bash', zsh: 'bash', ps1: 'powershell',
  psm1: 'powershell', sql: 'sql', css: 'css', scss: 'scss', html: 'xml', htm: 'xml', xml: 'xml', svg: 'xml', vue: 'xml', json: 'json', jsonc: 'json',
  yaml: 'yaml', yml: 'yaml', toml: 'ini', ini: 'ini', cfg: 'ini', conf: 'ini', md: 'markdown', mdx: 'markdown', diff: 'diff', patch: 'diff',
};

/** The language a file is written in, by its name or extension ('' for one this build does not highlight). */
export function languageForPath(path: string): string {
  const name = path.split(/[\\/]/).pop()?.toLowerCase() ?? '';
  if (name === 'dockerfile' || name.endsWith('.dockerfile')) return 'dockerfile';
  const dot = name.lastIndexOf('.');
  return dot > 0 ? (EXTENSIONS[name.slice(dot + 1)] ?? '') : '';
}

export const escapeHtml = (text: string) => text.replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]!);

/**
 * ``code`` highlighted as a whole (so a string or a comment over several lines keeps its colour) and
 * cut into its lines, each closing the spans still open at its end and opening them again on the
 * next. Escaped text, line by line, for a language this build does not know.
 */
export function highlightLines(code: string, language: string): string[] {
  const html = highlight(code, language);
  if (html === null) return code.split('\n').map(escapeHtml);
  const lines: string[] = [];
  const open: string[] = [];
  let line = '';
  for (const [part] of html.matchAll(/<span[^>]*>|<\/span>|\n|[^<\n]+/g)) {
    if (part === '\n') {
      lines.push(line + '</span>'.repeat(open.length));
      line = open.join('');
    } else {
      if (part.startsWith('<span')) open.push(part);
      else if (part === '</span>') open.pop();
      line += part;
    }
  }
  lines.push(line + '</span>'.repeat(open.length));
  return lines;
}
