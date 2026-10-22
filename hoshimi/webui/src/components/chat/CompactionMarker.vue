<script setup lang="ts">
import { computed, ref } from 'vue';
import type { CompactionStep } from '@/api/types';
import { compact, duration, elapsed } from '@/format';
import { useI18n } from '@/i18n';
import { AppIcon, Collapse, icons } from '@/ui';
import { useElapsed } from '@/ui/useElapsed';
// Not through '@/ui': markdown-it and highlight.js load with the Playground alone.
import Markdown from '@/ui/Markdown.vue';

/**
 * Where the conversation was compacted: a rule across the column with a line in its middle.
 * "Compacting context…" with a pulsing dot (and the time it has taken so far, ticking) while the
 * summary is written; once it is, what it saved ("Context compacted · 118K → 14.5K tokens"), opening on a press to the summary the model reads
 * from then on and the user's words kept beside it. A compaction that failed or was stopped says so
 * (the conversation went on as it was).
 */
/** ``settled``: the message holding it has ended, so a compaction still "running" was cut off (the server restarted). */
const props = defineProps<{ step: CompactionStep; settled?: boolean }>();
const { t } = useI18n();
const open = ref(false);
const id = `compaction-${Math.random().toString(36).slice(2, 10)}`;
const status = computed(() => (props.settled && props.step.status === 'running' ? 'stopped' : props.step.status));
const running = computed(() => status.value === 'running');
const sofar = useElapsed(() => props.step.at * 1000, () => running.value);

const why = computed(() => (props.step.trigger === 'auto' ? t('playground.compactedAuto') : props.step.trigger === 'overflow' ? t('playground.compactedOverflow') : ''));
const label = computed(() => {
  if (running.value) return t('playground.compacting');
  if (status.value === 'failed') return t('playground.compactFailed');
  if (status.value === 'stopped') return t('playground.compactStopped');
  return t('playground.compacted');
});
const detail = computed(() => {
  const s = props.step;
  if (status.value === 'failed') return s.error;
  if (running.value) return [sofar.value >= 1000 ? elapsed(sofar.value) : '', why.value].filter(Boolean).join(' · ');
  if (status.value !== 'done') return why.value;
  const saved = s.tokens_before && s.tokens_after ? t('playground.compactedTokens', { before: compact(s.tokens_before), after: compact(s.tokens_after) }) : '';
  return [saved, s.ms ? duration(s.ms) : '', why.value].filter(Boolean).join(' · ');
});
const expandable = computed(() => status.value === 'done' && !!props.step.summary);
</script>

<template>
  <div class="compaction" :class="status" role="note">
    <div class="rule">
      <span class="line" aria-hidden="true" />
      <component
        :is="expandable ? 'button' : 'span'"
        :type="expandable ? 'button' : undefined"
        class="head type-label-large"
        :class="{ 'state-layer': expandable }"
        :aria-expanded="expandable ? open : undefined"
        :aria-controls="expandable ? id : undefined"
        @click="expandable && (open = !open)"
      >
        <span v-if="running" class="dot" aria-hidden="true" />
        <AppIcon v-else :icon="status === 'failed' ? icons.AlertTriangle : icons.Layers" :size="18" />
        <span class="label">{{ label }}</span>
        <span v-if="detail" class="detail type-body-small">{{ detail }}</span>
        <AppIcon v-if="expandable" :icon="icons.ChevronDown" :size="18" class="chevron" :class="{ open }" />
      </component>
      <span class="line" aria-hidden="true" />
    </div>
    <Collapse :show="open && expandable">
      <div :id="id" class="summary app-scroll app-scroll-auto">
        <p class="type-label-medium muted">{{ t('playground.compactSummary', { model: step.model }) }}</p>
        <Markdown class="type-body-medium" :source="step.summary" :copy-label="t('playground.copyCode')" :copied-label="t('playground.codeCopied')" />
        <template v-if="step.recent.length">
          <p class="type-label-medium muted">{{ t('playground.compactKept', { n: step.recent.length }) }}</p>
          <ul class="recent type-body-small">
            <li v-for="(r, i) in step.recent" :key="i">{{ r }}</li>
          </ul>
        </template>
      </div>
    </Collapse>
  </div>
</template>

<style scoped>
.compaction { display: flex; flex-direction: column; gap: var(--app-space-2); margin: var(--app-space-2) 0; min-width: 0; }
.rule { display: flex; align-items: center; gap: var(--app-space-2); min-width: 0; }
.line { flex: 1; min-width: var(--app-space-4); height: 1px; background: var(--md-sys-color-outline-variant); }
.head {
  display: inline-flex; align-items: center; gap: var(--app-space-2); flex: 0 1 auto; min-width: 0; max-width: 85%; padding: 4px var(--app-space-3);
  border: 1px solid var(--md-sys-color-outline-variant); border-radius: var(--md-sys-shape-corner-full);
  background: var(--md-sys-color-surface-container-low); color: var(--md-sys-color-on-surface-variant); font: inherit;
}
button.head { cursor: pointer; }
.label { flex: none; }
.detail { min-width: 0; overflow: hidden; white-space: nowrap; text-overflow: ellipsis; opacity: 0.85; }
.chevron { flex: none; transition: transform var(--md-sys-motion-duration-short4) var(--md-sys-motion-easing-standard); }
.chevron.open { transform: rotate(180deg); }
.dot { flex: none; width: 8px; height: 8px; border-radius: 50%; background: var(--md-sys-color-tertiary); animation: compaction-pulse 1.5s ease-in-out infinite; }
@keyframes compaction-pulse { 50% { opacity: 0.35; } }
.done .head { color: var(--md-sys-color-on-surface); }
.failed .head { border-color: color-mix(in srgb, var(--md-sys-color-error) 40%, transparent); color: var(--md-sys-color-error); }
.stopped .head { opacity: 0.7; }
.summary {
  display: flex; flex-direction: column; gap: var(--app-space-2); max-height: 360px; padding: var(--app-space-3) var(--app-space-4);
  border-radius: var(--md-sys-shape-corner-medium); background: var(--md-sys-color-surface-container-low);
}
.summary p { margin: 0; }
.recent { display: flex; flex-direction: column; gap: var(--app-space-1); margin: 0; padding-inline-start: var(--app-space-5); white-space: pre-wrap; overflow-wrap: anywhere; }
@media (prefers-reduced-motion: reduce) { .dot { animation: none; } .chevron { transition: none; } }
</style>
