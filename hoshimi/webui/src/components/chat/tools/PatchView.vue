<script setup lang="ts">
import { computed, reactive } from 'vue';
import CodeFrame from '@/components/chat/tools/CodeFrame.vue';
import { numberLines, parsePatch, patchedFiles, type PatchFile } from '@/components/chat/tools/parse';
import { useI18n } from '@/i18n';
import { icons } from '@/ui';
import { escapeHtml, highlightLines, languageForPath } from '@/ui/highlight';

/**
 * An ``apply_patch`` call as the change it makes: how many files and lines, then each file in a frame
 * of its own (added, changed, renamed or deleted, with the lines it adds and removes) holding its
 * diff, highlighted in the file's language. Once the patch is applied the tool says where each hunk
 * landed, and its lines carry their numbers in the file before and after; a patch still being written
 * shows as far as it has come. A file with a long diff starts folded.
 */
const props = defineProps<{ input: string; meta: Record<string, unknown> }>();
const { t } = useI18n();
const FOLD_LINES = 160;

type Op = 'added' | 'modified' | 'renamed' | 'deleted';
interface Row {
  kind: 'context' | 'add' | 'remove' | 'hunk';
  html: string;
  old?: number;
  new?: number;
}
const files = computed(() => {
  const applied = patchedFiles(props.meta);
  return parsePatch(props.input).map((parsed, i) => {
    const done = applied[i];
    const file: PatchFile = numberLines(parsed, done?.hunks);
    const added = done?.added ?? file.added;
    const removed = done?.removed ?? file.removed;
    const path = file.moveTo || file.path;
    const language = languageForPath(path);
    // Each hunk highlighted whole, before and after, so a string or a comment across lines keeps its colour.
    const rows: Row[] = [];
    file.hunks.forEach((hunk, h) => {
      if (file.op === 'update' && (h > 0 || hunk.header)) rows.push({ kind: 'hunk', html: hunk.header ? escapeHtml(hunk.header) : '' });
      const before = highlightLines(hunk.lines.filter((l) => l.kind !== 'add').map((l) => l.text).join('\n'), language);
      const after = highlightLines(hunk.lines.filter((l) => l.kind !== 'remove').map((l) => l.text).join('\n'), language);
      let b = 0;
      let a = 0;
      for (const line of hunk.lines) {
        const html = line.kind === 'add' ? after[a++] : line.kind === 'remove' ? before[b++] : (b++, after[a++]);
        rows.push({ kind: line.kind, html: html ?? '', old: line.old, new: line.new });
      }
    });
    const op: Op = file.op === 'update' && file.moveTo && file.moveTo !== file.path ? 'renamed' : file.op === 'update' ? 'modified' : file.op === 'add' ? 'added' : 'deleted';
    const diff = file.hunks
      .map((h) => (file.op === 'update' ? [`@@${h.header ? ` ${h.header}` : ''}`] : []).concat(h.lines.map((l) => (l.kind === 'add' ? '+' : l.kind === 'remove' ? '-' : ' ') + l.text)).join('\n'))
      .join('\n');
    const numbered = rows.some((r) => r.old !== undefined || r.new !== undefined);
    return { key: `${i}:${file.path}`, file, path, op, added, removed, rows, diff, numbered };
  });
});
const totals = computed(() => ({
  files: files.value.length,
  added: files.value.reduce((n, f) => n + f.added, 0),
  removed: files.value.reduce((n, f) => n + f.removed, 0),
}));
const ICONS: Record<Op, typeof icons.File> = { added: icons.FilePlus, modified: icons.FileDiff, renamed: icons.FileSymlink, deleted: icons.FileMinus };

/** Which files the reader folded or unfolded; the rest start as their length says. */
const folded = reactive(new Map<string, boolean>());
const isFolded = (f: { key: string; rows: Row[] }) => folded.get(f.key) ?? f.rows.length > FOLD_LINES;
</script>

<template>
  <div class="patch">
    <p v-if="totals.files > 1" class="summary type-body-small">
      <span>{{ t('playground.toolView.filesChanged', { n: totals.files }) }}</span>
      <span class="count-add">+{{ totals.added }}</span>
      <span class="count-remove">−{{ totals.removed }}</span>
    </p>
    <CodeFrame
      v-for="f in files"
      :key="f.key"
      :icon="ICONS[f.op]"
      :title="f.path"
      :sub="f.op === 'renamed' ? t('playground.toolView.movedFrom', { path: f.file.path }) : t(`playground.toolView.${f.op}`)"
      :copy="f.diff"
      path
      :class="`op-${f.op}`"
    >
      <template #tags>
        <span v-if="f.added" class="code-tag count-add">+{{ f.added }}</span>
        <span v-if="f.removed" class="code-tag count-remove">−{{ f.removed }}</span>
      </template>
      <template v-if="f.rows.length" #tools>
        <button
          type="button"
          class="code-tool fold"
          :class="{ folded: isFolded(f) }"
          :aria-expanded="!isFolded(f)"
          :aria-label="isFolded(f) ? t('playground.toolView.unfold') : t('playground.toolView.fold')"
          :title="isFolded(f) ? t('playground.toolView.unfold') : t('playground.toolView.fold')"
          @click="folded.set(f.key, !isFolded(f))"
        >
          <svg aria-hidden="true" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="m6 9 6 6 6-6" /></svg>
        </button>
      </template>
      <div v-if="f.rows.length && !isFolded(f)" class="diff app-scroll app-scroll-chain app-scroll-auto">
        <table :class="{ numbered: f.numbered }">
          <tbody>
            <tr v-for="(row, r) in f.rows" :key="r" :class="row.kind">
              <template v-if="row.kind === 'hunk'">
                <td :colspan="f.numbered ? 4 : 2" class="hunk-head code-mono"><span class="dots" aria-hidden="true">⋯</span> <span v-html="row.html" /></td>
              </template>
              <template v-else>
                <td v-if="f.numbered" class="n" aria-hidden="true">{{ row.old ?? '' }}</td>
                <td v-if="f.numbered" class="n" aria-hidden="true">{{ row.new ?? '' }}</td>
                <td class="sign" aria-hidden="true">{{ row.kind === 'add' ? '+' : row.kind === 'remove' ? '−' : '' }}</td>
                <td class="text code-mono" v-html="row.html" />
              </template>
            </tr>
          </tbody>
        </table>
      </div>
      <p v-else-if="f.op === 'deleted'" class="note type-body-small">{{ t('playground.toolView.deletedLines', { n: f.removed }) }}</p>
    </CodeFrame>
  </div>
</template>

<style scoped>
.patch { display: flex; flex-direction: column; gap: var(--app-space-2); min-width: 0; }
.summary { display: flex; align-items: center; gap: var(--app-space-2); margin: 0; color: var(--md-sys-color-on-surface-variant); font-variant-numeric: tabular-nums; }
.summary span:first-child { color: var(--md-sys-color-on-surface); }
.fold svg { transition: transform var(--md-sys-motion-duration-short4) var(--md-sys-motion-easing-standard); transform: rotate(180deg); }
.fold.folded svg { transform: none; }
.op-added :deep(.code-icon) { color: var(--md-sys-color-primary); }
.op-deleted :deep(.code-icon) { color: var(--md-sys-color-error); }
/* The diff: lines wrap rather than scroll sideways, each with its numbers and its sign. */
.diff { max-height: 480px; }
table { width: 100%; border-collapse: collapse; table-layout: auto; }
td { padding: 0; vertical-align: top; }
.n {
  width: 1%; padding: 0 var(--app-space-2); color: var(--md-sys-color-on-surface-variant); opacity: 0.7; text-align: end; white-space: nowrap; user-select: none;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: var(--md-sys-typescale-body-small-size); line-height: 1.5; font-variant-numeric: tabular-nums;
}
.sign {
  width: 1%; padding: 0 4px 0 var(--app-space-2); color: var(--md-sys-color-on-surface-variant); user-select: none;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: var(--md-sys-typescale-body-small-size); line-height: 1.5;
}
.text { padding-inline-end: var(--app-space-3); white-space: pre-wrap; overflow-wrap: anywhere; }
tr.add { background: color-mix(in srgb, var(--md-sys-color-primary) 12%, transparent); }
tr.remove { background: color-mix(in srgb, var(--md-sys-color-error) 12%, transparent); }
tr.add .sign, tr.add .n { color: var(--md-sys-color-primary); }
tr.remove .sign, tr.remove .n { color: var(--md-sys-color-error); }
tr.add .n, tr.remove .n { opacity: 1; }
.hunk-head {
  padding: 2px var(--app-space-3); background: color-mix(in srgb, var(--md-sys-color-secondary) 10%, transparent); color: var(--md-sys-color-on-surface-variant);
  white-space: pre-wrap; overflow-wrap: anywhere;
}
.dots { color: var(--md-sys-color-secondary); }
tbody tr:first-child td { padding-top: var(--app-space-1); }
tbody tr:last-child td { padding-bottom: var(--app-space-1); }
.note { margin: 0; padding: var(--app-space-2) var(--app-space-3); color: var(--md-sys-color-on-surface-variant); }
@media (prefers-reduced-motion: reduce) { .fold svg { transition: none; } }
</style>
