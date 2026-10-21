<script setup lang="ts">
import { computed } from 'vue';

/**
 * A trend at a glance, for a stat tile: one 2px line in the de-emphasis tone with a 10% wash under
 * it, and the latest point marked in the accent. No axes: the tile's number is the value; the
 * table beside or under it carries the rest.
 */
const props = withDefaults(defineProps<{ values: number[]; height?: number; label?: string }>(), { height: 32, label: '' });
const W = 120;
const PAD = 4;
const points = computed(() => {
  const v = props.values;
  if (v.length < 2) return [];
  const max = Math.max(...v);
  const min = Math.min(0, ...v);
  const span = max - min || 1;
  return v.map((y, i) => [(i / (v.length - 1)) * (W - PAD * 2) + PAD, props.height - PAD - ((y - min) / span) * (props.height - PAD * 2)] as const);
});
const line = computed(() => points.value.map(([x, y], i) => `${i ? 'L' : 'M'}${x.toFixed(1)},${y.toFixed(1)}`).join(' '));
const area = computed(() => (points.value.length ? `${line.value} L${points.value.at(-1)![0].toFixed(1)},${props.height} L${points.value[0][0].toFixed(1)},${props.height} Z` : ''));
const last = computed(() => points.value.at(-1));
</script>

<template>
  <svg v-if="points.length" class="sparkline" :viewBox="`0 0 ${W} ${height}`" preserveAspectRatio="none" :height="height" role="img" :aria-label="label">
    <path :d="area" class="area" />
    <path :d="line" class="line" />
    <circle v-if="last" :cx="last[0]" :cy="last[1]" r="2.5" class="dot" />
  </svg>
</template>

<style scoped>
.sparkline { display: block; width: 100%; overflow: visible; }
.area { fill: var(--md-sys-color-primary); opacity: 0.1; }
.line { fill: none; stroke: var(--md-sys-color-outline); stroke-width: 2; stroke-linejoin: round; stroke-linecap: round; vector-effect: non-scaling-stroke; }
.dot { fill: var(--md-sys-color-primary); }
</style>
