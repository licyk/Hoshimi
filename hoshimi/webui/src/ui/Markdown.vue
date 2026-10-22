<script lang="ts">
import MarkdownIt from 'markdown-it';
import { highlight } from '@/ui/highlight';

/**
 * One renderer for every reply. The text is a model's, so it is never trusted: raw HTML is shown as
 * text (``html: false``), and markdown-it already refuses ``javascript:``, ``vbscript:`` and most
 * ``data:`` links. Single newlines break the line, as they do in a chat. Links open beside the
 * app and give the page they open no hold on it. Code blocks and tables scroll sideways in a lane
 * of their own (``.app-scroll``) rather than widen the conversation.
 *
 * A code block is highlighted when its fence names a language this build knows (``highlight.ts``)
 * and carries a head with that name and a copy button. The button is plain HTML in the rendered
 * string, so a click on it is caught on the whole reply and reported as ``copy`` with the code.
 */
const md = new MarkdownIt({ html: false, linkify: true, breaks: true });
const escape = md.utils.escapeHtml;

/** Lucide's copy icon, as the button is markup rather than a component. */
const COPY_ICON =
  '<svg aria-hidden="true" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">' +
  '<rect width="14" height="14" x="8" y="8" rx="2" ry="2"/><path d="M4 16c-1.1 0-2-.9-2-2V4c0-1.1.9-2 2-2h10c1.1 0 2 .9 2 2"/></svg>';

interface Env {
  copyLabel: string;
}

function codeBlock(code: string, info: string, env: Env): string {
  const language = info.trim().split(/\s+/)[0] ?? '';
  const highlighted = highlight(code, language);
  const label = escape(env.copyLabel);
  return (
    '<div class="code-block">' +
    `<div class="code-head"><span class="code-lang">${escape(language)}</span>` +
    `<button type="button" class="code-copy" data-copy aria-label="${label}" title="${label}">${COPY_ICON}</button></div>` +
    `<pre class="app-scroll app-scroll-auto"><code class="hljs${highlighted === null ? '' : ` language-${escape(language)}`}">${highlighted ?? escape(code)}</code></pre>` +
    '</div>\n'
  );
}

const link = md.renderer.rules.link_open ?? ((tokens, i, options, _env, self) => self.renderToken(tokens, i, options));
md.renderer.rules.link_open = (tokens, i, options, env, self) => {
  tokens[i].attrSet('target', '_blank');
  tokens[i].attrSet('rel', 'noopener noreferrer');
  return link(tokens, i, options, env, self);
};
md.renderer.rules.fence = (tokens, i, _options, env) => codeBlock(tokens[i].content, tokens[i].info, env);
md.renderer.rules.code_block = (tokens, i, _options, env) => codeBlock(tokens[i].content, '', env);
md.renderer.rules.table_open = () => '<div class="table app-scroll app-scroll-auto"><table>\n';
md.renderer.rules.table_close = () => '</table></div>\n';

export const renderMarkdown = (source: string, copyLabel = 'Copy'): string => md.render(source, { copyLabel } satisfies Env);
</script>

<script setup lang="ts">
import { computed } from 'vue';

const props = defineProps<{ source: string; copyLabel: string }>();
const emit = defineEmits<{ copy: [code: string] }>();
const html = computed(() => renderMarkdown(props.source, props.copyLabel));

function onClick(event: MouseEvent) {
  const button = (event.target as Element).closest('[data-copy]');
  const code = button?.closest('.code-block')?.querySelector('pre > code');
  // A fence's code ends with the newline before its closing fence; the copy leaves it out.
  if (code) emit('copy', (code.textContent ?? '').replace(/\n$/, ''));
}
</script>

<template>
  <!-- markdown-it's output, with raw HTML switched off (above): a reply's own tags are shown as text. -->
  <div class="markdown" @click="onClick" v-html="html" />
</template>

<style scoped>
.markdown { min-width: 0; overflow-wrap: anywhere; }
.markdown :deep(> :first-child) { margin-top: 0; }
.markdown :deep(> :last-child) { margin-bottom: 0; }
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
</style>
