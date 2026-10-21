<script setup lang="ts">
import { computed, type Component } from 'vue';
import AppIcon from '@/ui/AppIcon.vue';
import { useTween } from '@/ui/motion/useTween';
import Sparkline from '@/ui/Sparkline.vue';

/**
 * One headline number: a label, the value (gliding to each new value, so a live update reads as
 * a change rather than a flicker), an optional change against the previous period, a line of
 * detail and a 12-ish point trend. ``format`` turns the (tweened) number into text; a null value
 * shows a dash rather than a misleading zero.
 */
const props = withDefaults(
  defineProps<{
    label: string;
    value: number | null | undefined;
    format?: (n: number) => string;
    /** The change against the previous period, as a fraction (0.12 is +12 %); null when there is nothing to compare. */
    delta?: number | null;
    /** Whether a rise is good news (tokens served) or bad (failures, latency). */
    upIsGood?: boolean;
    deltaLabel?: string;
    detail?: string;
    trend?: number[];
    icon?: Component;
  }>(),
  { format: (n: number) => Math.round(n).toLocaleString(), delta: null, upIsGood: true, deltaLabel: '', detail: '', trend: () => [], icon: undefined },
);
const shown = useTween(() => props.value);
const text = computed(() => (shown.value === null || shown.value === undefined ? '—' : props.format(shown.value)));
const deltaText = computed(() => {
  const d = props.delta;
  if (d === null || d === undefined || !Number.isFinite(d)) return '';
  const pct = Math.round(d * 100);
  return pct === 0 ? '±0%' : `${pct > 0 ? '+' : '−'}${Math.abs(pct)}%`;
});
const tone = computed(() => {
  const d = props.delta ?? 0;
  if (!deltaText.value || Math.round(d * 100) === 0) return 'flat';
  return d > 0 === props.upIsGood ? 'good' : 'bad';
});
</script>

<template>
  <div class="stat-tile">
    <div class="head">
      <AppIcon v-if="icon" :icon="icon" :size="18" class="icon" />
      <span class="type-label-large muted label">{{ label }}</span>
    </div>
    <div class="value-row">
      <span class="value">{{ text }}</span>
      <span v-if="deltaText" class="delta type-label-medium" :class="tone" :title="deltaLabel">{{ deltaText }}</span>
    </div>
    <span v-if="detail" class="type-body-small muted detail">{{ detail }}</span>
    <Sparkline v-if="trend.length > 1" :values="trend" :label="label" class="trend" />
  </div>
</template>

<style scoped>
.stat-tile {
  display: flex; flex-direction: column; gap: var(--app-space-1); min-width: 0; padding: var(--app-space-4);
  border-radius: var(--md-sys-shape-corner-large); background: var(--md-sys-color-surface);
}
.head { display: flex; align-items: center; gap: var(--app-space-2); min-width: 0; }
.icon { color: var(--md-sys-color-on-surface-variant); }
.label, .detail { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.value-row { display: flex; align-items: baseline; gap: var(--app-space-2); flex-wrap: wrap; }
.value { font-size: var(--md-sys-typescale-headline-small-size); line-height: var(--md-sys-typescale-headline-small-line-height); font-weight: 500; }
.delta { padding: 0 var(--app-space-2); border-radius: var(--md-sys-shape-corner-full); }
.good { background: var(--md-sys-color-primary-container); color: var(--md-sys-color-on-primary-container); }
.bad { background: var(--md-sys-color-error-container); color: var(--md-sys-color-on-error-container); }
.flat { background: var(--md-sys-color-surface-container-high); color: var(--md-sys-color-on-surface-variant); }
.trend { margin-top: auto; padding-top: var(--app-space-2); }
</style>
