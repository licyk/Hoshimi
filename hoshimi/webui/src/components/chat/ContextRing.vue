<script setup lang="ts">
import { computed } from 'vue';
import { compact } from '@/format';
import { useI18n } from '@/i18n';

/**
 * How full the context window is, as a ring in the composer's row of tools: it fills as the
 * conversation grows (gliding to each new count), and turns to the warning colours as it nears the
 * point where the conversation is compacted. A press opens the status (``/status``).
 */
const props = defineProps<{ used: number; window: number; limit: number; estimate?: boolean }>();
defineEmits<{ click: [] }>();
const { t } = useI18n();

const R = 7;
const CIRCUMFERENCE = 2 * Math.PI * R;
const share = computed(() => (props.window > 0 ? Math.min(1, props.used / props.window) : 0));
const tone = computed(() => {
  const toLimit = props.limit > 0 ? props.used / props.limit : 0;
  return toLimit >= 0.9 ? 'error' : toLimit >= 0.7 ? 'warning' : 'primary';
});
const left = computed(() => Math.round((1 - share.value) * 100));
const text = computed(() =>
  t('playground.status.ring', { left: left.value, used: `${props.estimate ? '≈ ' : ''}${compact(props.used)}`, window: compact(props.window), limit: compact(props.limit) }),
);
</script>

<template>
  <button type="button" class="context-ring state-layer" :class="tone" :aria-label="text" :title="text" @click="$emit('click')">
    <svg width="20" height="20" viewBox="0 0 20 20" aria-hidden="true">
      <circle class="track" cx="10" cy="10" :r="R" />
      <circle class="arc" cx="10" cy="10" :r="R" :stroke-dasharray="`${share * CIRCUMFERENCE} ${CIRCUMFERENCE}`" />
    </svg>
    <span class="type-label-medium figure">{{ left }}%</span>
  </button>
</template>

<style scoped>
.context-ring {
  display: inline-flex; align-items: center; gap: 4px; flex: none; height: 32px; padding: 0 var(--app-space-2); border: 0;
  border-radius: var(--md-sys-shape-corner-full); background: transparent; color: var(--md-sys-color-on-surface-variant); cursor: pointer;
}
svg { transform: rotate(-90deg); }
.track { fill: none; stroke: var(--md-sys-color-surface-container-highest); stroke-width: 3; }
.arc {
  fill: none; stroke: var(--md-sys-color-primary); stroke-width: 3; stroke-linecap: round;
  transition: stroke-dasharray var(--md-sys-motion-duration-medium2) var(--md-sys-motion-easing-emphasized), stroke var(--md-sys-motion-duration-short4) var(--md-sys-motion-easing-standard);
}
.warning .arc { stroke: var(--md-sys-color-tertiary); }
.error .arc { stroke: var(--md-sys-color-error); }
.error { color: var(--md-sys-color-error); }
.figure { font-variant-numeric: tabular-nums; }
@media (max-width: 599px) { .figure { display: none; } .context-ring { width: 32px; justify-content: center; padding: 0; } }
@media (prefers-reduced-motion: reduce) { .arc { transition: none; } }
</style>
