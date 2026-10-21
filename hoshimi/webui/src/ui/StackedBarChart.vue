<script setup lang="ts">
import { computed, ref, useId } from 'vue';
import { useElementWidth } from '@/ui/useElementWidth';

export interface StackSeries {
  key: string;
  label: string;
  /** The chart slot (1-8): a series keeps its colour wherever it appears, whatever else is shown. */
  slot: number;
}
export interface StackPoint {
  key: string;
  /** The axis label (a short date or hour). */
  label: string;
  /** The tooltip's heading; the axis label when not given. */
  title?: string;
  /** One value per series, in the series' order. */
  values: number[];
}

/**
 * Columns split into stacked parts over time: tokens per hour or day, by kind. Bars are at most
 * 24px wide with a 4px rounded top on a square base, parts separated by a 2px gap of the surface;
 * the grid is a recessive hairline and the y ticks are compact round numbers. Pointing at or
 * focusing a column (arrow keys move along) shows every part's value with its total; the legend
 * names the series and a hidden table carries every number for assistive technology. While data
 * reloads (``busy``) the previous render stays, dimmed, rather than flashing.
 */
const props = withDefaults(defineProps<{ series: StackSeries[]; points: StackPoint[]; label: string; format?: (n: number) => string; height?: number; busy?: boolean; totalLabel?: string }>(), {
  format: (n: number) => n.toLocaleString(),
  height: 220,
  busy: false,
  totalLabel: 'Total',
});
const PAD = { l: 48, r: 8, t: 12, b: 24 };
const BAR_MAX = 24;
const GAP = 2;
const root = ref<HTMLElement | null>(null);
const width = useElementWidth(root, 640);
const active = ref<number | null>(null);
// Clip paths are found by id across the whole document: one per chart instance, whatever its label.
const uid = useId();

const totals = computed(() => props.points.map((p) => p.values.reduce((a, b) => a + Math.max(0, b), 0)));
/** A round step near a quarter of the largest column, so the ticks read 0 / 5K / 10K / 15K. */
const scale = computed(() => {
  const max = Math.max(0, ...totals.value);
  if (!max) return { top: 1, ticks: [0] };
  const raw = max / 4;
  const mag = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => s >= raw) ?? 10 * mag;
  const top = Math.ceil(max / step) * step;
  return { top, ticks: Array.from({ length: Math.round(top / step) + 1 }, (_, i) => i * step) };
});
const plotW = computed(() => Math.max(1, width.value - PAD.l - PAD.r));
const plotH = computed(() => props.height - PAD.t - PAD.b);
const slotW = computed(() => plotW.value / Math.max(1, props.points.length));
const barW = computed(() => Math.max(2, Math.min(BAR_MAX, slotW.value * 0.7)));
const y = (v: number) => PAD.t + plotH.value - (v / scale.value.top) * plotH.value;

const bars = computed(() =>
  props.points.map((p, i) => {
    const x = PAD.l + i * slotW.value + (slotW.value - barW.value) / 2;
    let base = 0;
    const parts = props.series.flatMap((s, j) => {
      const v = Math.max(0, p.values[j] ?? 0);
      if (!v) return [];
      const bottom = y(base);
      base += v;
      const top = y(base);
      // The gap is cut from the part's bottom, so the base of the column stays on the axis.
      const h = Math.max(0, bottom - top - (j > 0 && bottom < PAD.t + plotH.value ? GAP : 0));
      return [{ key: s.key, slot: s.slot, y: top, h }];
    });
    const top = y(base);
    // The column's outline: square at the base, 4px round at the top (or less on a short column).
    const r = Math.min(4, barW.value / 2, PAD.t + plotH.value - top);
    const b = PAD.t + plotH.value;
    const clip = base ? `M${x},${b} V${top + r} Q${x},${top} ${x + r},${top} H${x + barW.value - r} Q${x + barW.value},${top} ${x + barW.value},${top + r} V${b} Z` : '';
    return { key: p.key, x, parts, clip, total: totals.value[i] };
  }),
);
const labelEvery = computed(() => Math.max(1, Math.ceil(props.points.length / Math.max(2, Math.floor(plotW.value / 72)))));

const tip = computed(() => {
  const i = active.value;
  if (i === null || !props.points[i]) return null;
  const p = props.points[i];
  const center = PAD.l + i * slotW.value + slotW.value / 2;
  return {
    title: p.title ?? p.label,
    rows: props.series.map((s, j) => ({ key: s.key, label: s.label, slot: s.slot, value: props.format(p.values[j] ?? 0) })),
    total: props.format(totals.value[i]),
    // Kept inside the chart: flips to the column's left past the middle.
    style: center > width.value / 2 ? { right: `${width.value - center + 12}px` } : { left: `${center + 12}px` },
  };
});
function onKey(e: KeyboardEvent) {
  const n = props.points.length;
  if (!n) return;
  if (e.key === 'ArrowRight' || e.key === 'ArrowLeft') {
    e.preventDefault();
    const step = e.key === 'ArrowRight' ? 1 : -1;
    active.value = active.value === null ? (step > 0 ? 0 : n - 1) : Math.min(n - 1, Math.max(0, active.value + step));
  } else if (e.key === 'Escape') active.value = null;
}
</script>

<template>
  <figure ref="root" class="stacked" :class="{ busy }">
    <figcaption class="legend type-label-medium">
      <span v-for="s in series" :key="s.key" class="key"><span class="swatch" :style="{ background: `var(--app-chart-${s.slot})` }" />{{ s.label }}</span>
    </figcaption>
    <div class="plot">
      <svg
        :viewBox="`0 0 ${width} ${height}`"
        :width="width"
        :height="height"
        role="img"
        :aria-label="label"
        tabindex="0"
        @keydown="onKey"
        @blur="active = null"
        @pointerleave="active = null"
      >
        <g class="grid">
          <line v-for="t in scale.ticks" :key="`g${t}`" :x1="PAD.l" :x2="width - PAD.r" :y1="y(t)" :y2="y(t)" />
        </g>
        <g class="ticks num">
          <text v-for="t in scale.ticks" :key="`t${t}`" :x="PAD.l - 8" :y="y(t) + 4" text-anchor="end">{{ format(t) }}</text>
          <template v-for="(p, i) in points" :key="`x${p.key}`">
            <text v-if="i % labelEvery === 0" :x="PAD.l + i * slotW + slotW / 2" :y="height - 6" text-anchor="middle">{{ p.label }}</text>
          </template>
        </g>
        <defs>
           <clipPath v-for="(b, i) in bars" :id="`${uid}-clip-${i}`" :key="`c${b.key}`"><path :d="b.clip" /></clipPath>
        </defs>
        <g v-for="(b, i) in bars" :key="b.key" class="bar" :class="{ dim: active !== null && active !== i }" :clip-path="`url(#${uid}-clip-${i})`">
          <rect v-for="part in b.parts" :key="part.key" :x="b.x" :width="barW" :style="{ y: `${part.y}px`, height: `${part.h}px`, fill: `var(--app-chart-${part.slot})` }" />
        </g>
        <!-- Hit targets the full height of each slot: the reader aims at a date, not a 2px part. -->
        <rect
          v-for="(p, i) in points"
          :key="`h${p.key}`"
          class="hit"
          :x="PAD.l + i * slotW"
          :y="PAD.t"
          :width="slotW"
          :height="plotH"
          @pointerenter="active = i"
        />
      </svg>
      <div v-if="tip" class="tip type-body-small" :style="tip.style" aria-hidden="true">
        <span class="type-label-large">{{ tip.title }}</span>
        <span v-for="r in tip.rows" :key="r.key" class="tip-row">
          <span class="line-key" :style="{ background: `var(--app-chart-${r.slot})` }" />
          <strong class="num">{{ r.value }}</strong>
          <span class="muted">{{ r.label }}</span>
        </span>
        <span class="tip-row total"><strong class="num">{{ tip.total }}</strong><span class="muted">{{ totalLabel }}</span></span>
      </div>
    </div>
    <table class="visually-hidden">
      <caption>{{ label }}</caption>
      <thead><tr><th scope="col" /><th v-for="s in series" :key="s.key" scope="col">{{ s.label }}</th><th scope="col">{{ totalLabel }}</th></tr></thead>
      <tbody>
        <tr v-for="(p, i) in points" :key="p.key">
          <th scope="row">{{ p.title ?? p.label }}</th>
          <td v-for="(s, j) in series" :key="s.key">{{ format(p.values[j] ?? 0) }}</td>
          <td>{{ format(totals[i]) }}</td>
        </tr>
      </tbody>
    </table>
  </figure>
</template>

<style scoped>
.stacked { margin: 0; display: flex; flex-direction: column; gap: var(--app-space-3); min-width: 0; transition: opacity var(--md-sys-motion-duration-short4) var(--md-sys-motion-easing-standard); }
.stacked.busy { opacity: 0.6; }
.legend { display: flex; flex-wrap: wrap; gap: var(--app-space-2) var(--app-space-4); color: var(--md-sys-color-on-surface-variant); }
.key { display: inline-flex; align-items: center; gap: var(--app-space-2); }
.swatch { width: 10px; height: 10px; border-radius: 2px; }
.plot { position: relative; min-width: 0; }
svg { display: block; max-width: 100%; overflow: visible; }
svg:focus-visible { outline-offset: 4px; border-radius: var(--md-sys-shape-corner-small); }
.grid line { stroke: var(--md-sys-color-surface-container-highest); stroke-width: 1; }
.ticks text { fill: var(--md-sys-color-on-surface-variant); font-size: 11px; }
.bar rect { transition: y var(--md-sys-motion-duration-medium2) var(--md-sys-motion-easing-emphasized), height var(--md-sys-motion-duration-medium2) var(--md-sys-motion-easing-emphasized); }
.bar { transition: opacity var(--md-sys-motion-duration-short3) var(--md-sys-motion-easing-standard); }
.bar.dim { opacity: 0.45; }
.hit { fill: transparent; }
.tip {
  position: absolute; top: 0; z-index: var(--app-z-dropdown); display: flex; flex-direction: column; gap: var(--app-space-1); min-width: 160px; padding: var(--app-space-2) var(--app-space-3);
  border-radius: var(--md-sys-shape-corner-small); background: var(--md-sys-color-inverse-surface); color: var(--md-sys-color-inverse-on-surface); box-shadow: var(--app-elevation-2); pointer-events: none;
}
.tip .muted { color: inherit; opacity: 0.8; }
.tip-row { display: grid; grid-template-columns: 12px auto 1fr; align-items: center; gap: var(--app-space-2); }
.tip-row.total { grid-template-columns: 12px auto 1fr; padding-top: var(--app-space-1); border-top: 1px solid color-mix(in srgb, currentColor 24%, transparent); }
.tip-row.total > :first-child { grid-column: 2; }
.line-key { width: 12px; height: 2px; border-radius: 1px; }
</style>
