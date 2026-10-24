<script setup lang="ts">
import { computed } from 'vue';
import CodeFrame from '@/components/chat/tools/CodeFrame.vue';
import RawOutput from '@/components/chat/tools/RawOutput.vue';
import { parseRead } from '@/components/chat/tools/parse';
import { useI18n } from '@/i18n';
import { AppIcon, icons } from '@/ui';
import { highlightLines, languageForPath, languageName } from '@/ui/highlight';

/**
 * What ``read`` came back with. A file: its lines beside their numbers (the tool's ``L12:`` taken
 * off), highlighted in the file's language, with which lines these are of how many. A directory:
 * its folders and files, folders first as the tool lists them, and how many more it left out.
 */
const props = defineProps<{ path: string; output: string; meta: Record<string, unknown>; failed: boolean }>();
const { t } = useI18n();

const result = computed(() => (props.failed ? null : parseRead(props.output)));
const shownPath = computed(() => (typeof props.meta.path === 'string' && props.meta.path) || props.path);
const language = computed(() => languageForPath(shownPath.value));

const file = computed(() => {
  const r = result.value;
  if (r?.kind !== 'file') return null;
  const html = highlightLines(r.lines.map((l) => l.text).join('\n'), language.value);
  const from = r.lines[0].n;
  const to = r.lines[r.lines.length - 1].n;
  const total = r.total ?? (typeof props.meta.lines === 'number' ? props.meta.lines : null);
  const range =
    from === 1 && total === to ? t('playground.toolView.lines', { n: to })
    : total !== null ? t('playground.toolView.lineRange', { from, to, total })
    : t('playground.toolView.lineRangeOpen', { from, to });
  return { rows: r.lines.map((l, i) => ({ ...l, html: html[i] ?? '' })), range, text: r.lines.map((l) => l.text).join('\n'), width: String(to).length };
});

const dir = computed(() => {
  const r = result.value;
  if (r?.kind !== 'dir') return null;
  const folders = r.entries.filter((e) => e.dir).length;
  return { ...r, folders, files: r.entries.length - folders };
});
const FILE_ICONS: Record<string, typeof icons.File> = { json: icons.FileJson, md: icons.FileText, txt: icons.FileText, png: icons.Image, jpg: icons.Image, jpeg: icons.Image, gif: icons.Image, svg: icons.Image, webp: icons.Image };
const fileIcon = (name: string) => FILE_ICONS[name.split('.').pop()?.toLowerCase() ?? ''] ?? (languageForPath(name) ? icons.FileCode : icons.File);
</script>

<template>
  <CodeFrame v-if="file" :icon="icons.FileCode" :title="shownPath" :sub="languageName(language)" :copy="file.text" path>
    <template #tags><span class="code-tag">{{ file.range }}</span></template>
    <div class="lines app-scroll app-scroll-chain app-scroll-auto" :style="{ '--gutter': `${file.width}ch` }">
      <div v-for="row in file.rows" :key="row.n" class="file-line">
        <span class="n" aria-hidden="true">{{ row.n }}</span>
        <code class="text code-mono"><span v-html="row.html" /><span v-if="row.cut" class="cut" :title="t('playground.toolView.moreChars', { n: row.cut })">+{{ row.cut }}</span></code>
      </div>
    </div>
  </CodeFrame>
  <CodeFrame v-else-if="dir" :icon="icons.FolderOpen" :title="dir.path" :copy="dir.entries.map((e) => e.name + (e.dir ? '/' : '')).join('\n')" path>
    <template #tags>
      <span v-if="dir.folders" class="code-tag">{{ t('playground.toolView.folders', { n: dir.folders }) }}</span>
      <span v-if="dir.files" class="code-tag">{{ t('playground.toolView.files', { n: dir.files }) }}</span>
    </template>
    <ul v-if="dir.entries.length" class="entries app-scroll app-scroll-chain app-scroll-auto type-body-small">
      <li v-for="entry in dir.entries" :key="entry.name" :class="{ dir: entry.dir }">
        <AppIcon :icon="entry.dir ? icons.Folder : fileIcon(entry.name)" :size="18" />
        <span class="name">{{ entry.name }}<template v-if="entry.dir">/</template></span>
      </li>
    </ul>
    <p v-else class="note type-body-small">{{ t('playground.toolView.emptyDir') }}</p>
    <p v-if="dir.more" class="note more type-body-small">{{ t('playground.toolView.moreEntries', { n: dir.more }) }}</p>
  </CodeFrame>
  <CodeFrame v-else-if="result?.kind === 'empty'" :icon="icons.File" :title="result.path" path>
    <p class="note type-body-small">{{ t('playground.toolView.emptyFile') }}</p>
  </CodeFrame>
  <RawOutput v-else-if="output" :text="output" :failed="failed" />
</template>

<style scoped>
/* Lines wrap rather than scroll sideways (an area scrolls one way), each beside its number. */
.lines { max-height: 420px; padding: var(--app-space-1) 0; }
.file-line { display: grid; grid-template-columns: calc(var(--gutter) + var(--app-space-4)) minmax(0, 1fr); }
.n {
  padding: 0 var(--app-space-2) 0 var(--app-space-2); color: var(--md-sys-color-on-surface-variant); opacity: 0.7; text-align: end; user-select: none;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: var(--md-sys-typescale-body-small-size); line-height: 1.5; font-variant-numeric: tabular-nums;
}
.text { display: block; padding-inline: var(--app-space-2) var(--app-space-3); white-space: pre-wrap; overflow-wrap: anywhere; min-height: 1.5em; }
.file-line:hover { background: color-mix(in srgb, var(--md-sys-color-on-surface) 4%, transparent); }
.cut {
  margin-inline-start: var(--app-space-1); padding: 0 4px; border-radius: var(--md-sys-shape-corner-extra-small);
  background: var(--md-sys-color-surface-container-highest); color: var(--md-sys-color-on-surface-variant); font-style: italic;
}
.entries {
  display: grid; grid-template-columns: repeat(auto-fill, minmax(180px, 1fr)); gap: 2px var(--app-space-3); max-height: 320px; margin: 0;
  padding: var(--app-space-2) var(--app-space-3); list-style: none;
}
.entries li { display: flex; align-items: center; gap: var(--app-space-2); min-width: 0; color: var(--md-sys-color-on-surface); }
.entries li :deep(svg) { flex: none; width: 16px; height: 16px; color: var(--md-sys-color-on-surface-variant); }
.entries li.dir :deep(svg) { color: var(--md-sys-color-primary); }
.entries li.dir .name { font-weight: 500; }
.name { min-width: 0; overflow: hidden; white-space: nowrap; text-overflow: ellipsis; font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }
.note { margin: 0; padding: var(--app-space-2) var(--app-space-3); color: var(--md-sys-color-on-surface-variant); }
.note.more { padding-top: 0; }
</style>
