<script setup lang="ts">
import { computed } from 'vue';
import type { ContextView } from '@/api/types';
import { compact, elapsed, percent } from '@/format';
import { useI18n } from '@/i18n';
import { AppIcon, TweenNumber, icons } from '@/ui';

/**
 * The conversation's state at a glance (``/status``): the model and its effort, how full the
 * context window is (a bar with a mark where the conversation is compacted), what the last request
 * and the whole conversation took (cached input among it), its compactions, how long its replies
 * worked, and the tools it may call. A count the station did not report is an estimate, marked ≈.
 * Numbers glide to new values as a reply under way reports them.
 */
const props = defineProps<{ view: ContextView; effortLabel: string; tools: string[]; approvalLabel: string }>();
const { t } = useI18n();

const share = computed(() => (props.view.window > 0 ? Math.min(1, props.view.used / props.view.window) : 0));
const mark = computed(() => (props.view.window > 0 ? props.view.limit / props.view.window : 1));
const tone = computed(() => {
  const toLimit = props.view.limit > 0 ? props.view.used / props.view.limit : 0;
  return toLimit >= 0.9 ? 'error' : toLimit >= 0.7 ? 'warning' : 'primary';
});
const left = computed(() => Math.max(0, props.view.window - props.view.used));
const approx = computed(() => (props.view.used_source === 'estimate' ? '≈ ' : ''));
const cacheRate = computed(() => (props.view.totals.input > 0 ? props.view.totals.cached / props.view.totals.input : null));
const windowNote = computed(() => (props.view.window_source === 'learned' ? t('playground.status.windowLearned') : props.view.window_source === 'default' ? t('playground.status.windowDefault') : ''));
const tokens = (n: number) => compact(n);
</script>

<template>
  <div class="status-panel">
    <div class="model">
      <AppIcon :icon="icons.Cpu" :size="18" class="muted" />
      <span class="type-title-small name">{{ view.model || t('playground.noModel') }}</span>
      <span v-if="view.provider" class="type-body-small muted">{{ view.provider }}</span>
      <span class="badge type-label-small">{{ effortLabel }}</span>
    </div>

    <section class="context" :class="tone">
      <div class="line">
        <span class="type-label-large">{{ t('playground.status.context') }}</span>
        <span class="type-body-medium figure">
          <TweenNumber :value="Math.round((1 - share) * 100)" :format="(n) => `${Math.round(n)}%`" /> {{ t('playground.status.left') }}
        </span>
      </div>
      <div class="bar" role="img" :aria-label="t('playground.status.contextAria', { used: tokens(view.used), window: tokens(view.window), limit: tokens(view.limit) })">
        <span class="fill" :style="{ width: `${share * 100}%` }" />
        <span class="limit" :style="{ insetInlineStart: `${mark * 100}%` }" :title="t('playground.status.compactsAt', { limit: tokens(view.limit) })" />
      </div>
      <div class="line type-body-small muted">
        <span>{{ approx }}{{ t('playground.status.used', { used: tokens(view.used), window: tokens(view.window) }) }} · {{ t('playground.status.free', { left: tokens(left) }) }}</span>
        <span>{{ view.auto_compact ? t('playground.status.compactsAt', { limit: tokens(view.limit) }) : t('playground.status.autoOff') }}</span>
      </div>
      <p v-if="windowNote" class="note type-body-small muted">{{ windowNote }}</p>
    </section>

    <dl class="facts type-body-medium">
      <div>
        <dt class="muted">{{ t('playground.status.lastRequest') }}</dt>
        <dd v-if="view.last.input || view.last.output">
          {{ t('playground.status.inOut', { input: tokens(view.last.input), output: tokens(view.last.output) }) }}
          <span v-if="view.last.cached" class="muted"> · {{ t('playground.status.cached', { cached: tokens(view.last.cached) }) }}</span>
        </dd>
        <dd v-else class="muted">{{ t('playground.status.notReported') }}</dd>
      </div>
      <div>
        <dt class="muted">{{ t('playground.status.conversation') }}</dt>
        <dd>
          {{ t('playground.status.inOut', { input: tokens(view.totals.input), output: tokens(view.totals.output) }) }}
          <span v-if="cacheRate !== null" class="muted"> · {{ t('playground.status.cacheRate', { rate: percent(cacheRate, 0) }) }}</span>
          <span v-if="view.totals.reasoning" class="muted"> · {{ t('playground.status.reasoning', { reasoning: tokens(view.totals.reasoning) }) }}</span>
        </dd>
      </div>
      <div>
        <dt class="muted">{{ t('playground.status.compactions') }}</dt>
        <dd>
          {{ view.compactions ? t('playground.status.compactionCount', { n: view.compactions }) : t('playground.status.none') }}
          <span v-if="view.last_compaction" class="muted">
            · {{ t('playground.compactedTokens', { before: tokens(view.last_compaction.tokens_before), after: tokens(view.last_compaction.tokens_after) }) }}
          </span>
        </dd>
      </div>
      <div>
        <dt class="muted">{{ t('playground.status.runtime') }}</dt>
        <dd>
          <template v-if="view.total_duration_ms">
            {{ t('playground.status.lastReply', { time: elapsed(view.last_duration_ms) }) }}
            <span class="muted"> · {{ t('playground.status.allReplies', { time: elapsed(view.total_duration_ms), n: view.replies }) }}</span>
          </template>
          <span v-else class="muted">{{ t('playground.status.none') }}</span>
        </dd>
      </div>
      <div>
        <dt class="muted">{{ t('playground.tools') }}</dt>
        <dd>
          {{ tools.length ? tools.join(', ') : t('playground.toolsOff') }}
          <span v-if="tools.length" class="muted"> · {{ approvalLabel }}</span>
        </dd>
      </div>
    </dl>
  </div>
</template>

<style scoped>
.status-panel { display: flex; flex-direction: column; gap: var(--app-space-3); min-width: 0; }
.model { display: flex; align-items: center; flex-wrap: wrap; gap: var(--app-space-2); min-width: 0; }
.name { min-width: 0; overflow-wrap: anywhere; }
.badge { padding: 2px var(--app-space-2); border-radius: var(--md-sys-shape-corner-full); background: var(--md-sys-color-secondary-container); color: var(--md-sys-color-on-secondary-container); }
.context { display: flex; flex-direction: column; gap: var(--app-space-1); }
.line { display: flex; align-items: baseline; justify-content: space-between; flex-wrap: wrap; gap: var(--app-space-2); }
.figure { font-variant-numeric: tabular-nums; }
.bar { position: relative; height: 8px; border-radius: var(--md-sys-shape-corner-full); background: var(--md-sys-color-surface-container-highest); overflow: visible; }
.fill {
  position: absolute; inset-block: 0; inset-inline-start: 0; border-radius: inherit; background: var(--md-sys-color-primary);
  transition: width var(--md-sys-motion-duration-medium2) var(--md-sys-motion-easing-emphasized), background-color var(--md-sys-motion-duration-short4) var(--md-sys-motion-easing-standard);
}
.warning .fill { background: var(--md-sys-color-tertiary); }
.error .fill { background: var(--md-sys-color-error); }
/* Where the conversation is compacted. */
.limit { position: absolute; top: -3px; bottom: -3px; width: 2px; margin-inline-start: -1px; border-radius: 1px; background: var(--md-sys-color-on-surface-variant); opacity: 0.7; }
.note { margin: 0; }
.facts { display: grid; grid-template-columns: max-content 1fr; gap: var(--app-space-2) var(--app-space-4); margin: 0; }
.facts > div { display: contents; }
.facts dt { white-space: nowrap; }
.facts dd { margin: 0; min-width: 0; overflow-wrap: anywhere; font-variant-numeric: tabular-nums; }
@media (max-width: 420px) {
  .facts { grid-template-columns: 1fr; gap: 2px; }
  .facts dd { margin-bottom: var(--app-space-2); }
}
@media (prefers-reduced-motion: reduce) { .fill { transition: none; } }
</style>
