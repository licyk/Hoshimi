<script setup lang="ts">
import { computed, ref } from 'vue';
import { useElementWidth } from '@/ui/useElementWidth';

/**
 * A trend at a glance, for a stat tile: one 2px line in the de-emphasis tone with a 10% wash under
 * it, and the latest point marked in the accent. No axes: the tile's number is the value; the
 * table beside or under it carries the rest. It is drawn at its own width, one unit a pixel each
 * way: a drawing stretched to fit would turn the dot into an oval.
 */
const props = withDefaults(defineProps<{ values: number[]; height?: number; label?: string }>(), { height: 32, label: '' });
const PAD = 4;
const svg = ref<SVGSVGElement | null>(null);
const width = useElementWidth(svg, 120);
const W = computed(() => Math.max(width.value, PAD * 2 + 1));
const points = computed(() => {
  const v = props.values;
  if (v.length < 2) return [];
  const max = Math.max(...v);
  const min = Math.min(0, ...v);
  const span = max - min || 1;
  return v.map((y, i) => [(i / (v.length - 1)) * (W.value - PAD * 2) + PAD, props.height - PAD - ((y - min) / span) * (props.height - PAD * 2)] as const);
});
const line = computed(() => points.value.map(([x, y], i) => `${i ? 'L' : 'M'}${x.toFixed(1)},${y.toFixed(1)}`).join(' '));
const area = computed(() => (points.value.length ? `${line.value} L${points.value.at(-1)![0].toFixed(1)},${props.height} L${points.value[0][0].toFixed(1)},${props.height} Z` : ''));
const last = computed(() => points.value.at(-1));
</script>

<template>
  <svg v-if="points.length" ref="svg" class="sparkline morph" :viewBox="`0 0 ${W} ${height}`" :height="height" role="img" :aria-label="label">
    <path :d="area" class="area" :style="{ d: area ? `path('${area}')` : undefined }" />
    <path :d="line" class="line" :style="{ d: line ? `path('${line}')` : undefined }" />
    <circle v-if="last" :cx="last[0]" :cy="last[1]" r="2.5" class="dot" :style="{ cx: `${last[0]}px`, cy: `${last[1]}px` }" />
  </svg>
</template>

<style scoped>
.sparkline { display: block; width: 100%; overflow: visible; }
/* A new range reshapes the line rather than replacing it (where the browser animates ``d`` and the
   point count is unchanged; otherwise it is simply redrawn). */
.area, .line, .dot { transition: d var(--md-sys-motion-duration-medium4) var(--md-sys-motion-easing-emphasized), cx var(--md-sys-motion-duration-medium4) var(--md-sys-motion-easing-emphasized), cy var(--md-sys-motion-duration-medium4) var(--md-sys-motion-easing-emphasized); }
.area { fill: var(--md-sys-color-primary); opacity: 0.1; }
.line { fill: none; stroke: var(--md-sys-color-outline); stroke-width: 2; stroke-linejoin: round; stroke-linecap: round; vector-effect: non-scaling-stroke; }
.dot { fill: var(--md-sys-color-primary); }
</style>
