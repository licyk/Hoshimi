<script setup lang="ts">
import { computed, ref } from 'vue';
import { useElementWidth } from '@/ui/useElementWidth';

export interface Series {
  label: string;
  points: [number, number][];
  /** The chart slot (1-8, ``--app-chart-n``); the series' position when not given. */
  slot?: number;
}

/**
 * A plain SVG line chart: a few series on one shared axis (never two y scales), 2px lines, a
 * recessive hairline grid and a legend. A crosshair follows the pointer, snapping to the nearest
 * x, with a tooltip listing every series there; arrow keys move it when the chart has focus.
 * ``xLabels`` names the x positions (an hour, a day) for the axis and the tooltip; ``format``
 * prints the y values. A point whose value is not finite (NaN, no measurement) breaks its line.
 * It is drawn at its own width and ``height``, one unit a pixel each way, so a marker stays round
 * and the axis labels keep their size whatever the chart's width.
 */
const props = withDefaults(defineProps<{ series: Series[]; xLabel?: string; yLabel?: string; height?: number; emptyText?: string; xLabels?: string[]; format?: (n: number) => string }>(), {
  xLabel: '',
  yLabel: '',
  height: 240,
  emptyText: '',
  xLabels: undefined,
  format: undefined,
});
const PAD = { l: 48, r: 12, t: 12, b: 28 };
const svg = ref<SVGSVGElement | null>(null);
const width = useElementWidth(svg, 640);
const W = computed(() => Math.max(width.value, PAD.l + PAD.r + 1));
const slotOf = (s: Series, i: number) => s.slot ?? i + 1;
const fmt = (v: number) => (props.format ? props.format(v) : Math.abs(v) >= 100 ? v.toFixed(0) : Math.abs(v) >= 1 ? v.toFixed(1) : v.toFixed(3));

const bounds = computed(() => {
  const all = props.series.flatMap((s) => s.points);
  if (!all.length) return null;
  let [x0, x1, y0, y1] = [Infinity, -Infinity, Infinity, -Infinity];
  for (const [x, y] of all) {
    x0 = Math.min(x0, x);
    x1 = Math.max(x1, x);
    if (!Number.isFinite(y)) continue;
    y0 = Math.min(y0, y);
    y1 = Math.max(y1, y);
  }
  if (!Number.isFinite(y0)) return null;
  // Measures like latency are read from zero, so a flat line does not look like a cliff.
  y0 = Math.min(0, y0);
  if (x1 === x0) x1 = x0 + 1;
  if (y1 === y0) y1 = y0 + 1;
  return { x0, x1, y0, y1 };
});

const sx = (x: number) => (bounds.value ? PAD.l + ((x - bounds.value.x0) / (bounds.value.x1 - bounds.value.x0)) * (W.value - PAD.l - PAD.r) : 0);
const sy = (y: number) => (bounds.value ? PAD.t + (1 - (y - bounds.value.y0) / (bounds.value.y1 - bounds.value.y0)) * (props.height - PAD.t - PAD.b) : 0);

/** At most 400 points per line: a long run is thinned by stride, keeping the last point. */
function path(points: [number, number][]): string {
  const stride = Math.max(1, Math.ceil(points.length / 400));
  const kept = points.filter((_, i) => i % stride === 0 || i === points.length - 1);
  let out = '';
  let pen = false;
  for (const [x, y] of kept) {
    if (!Number.isFinite(y)) {
      pen = false;
      continue;
    }
    out += `${pen ? 'L' : 'M'}${sx(x).toFixed(1)},${sy(y).toFixed(1)} `;
    pen = true;
  }
  return out.trim();
}

const xName = (v: number) => props.xLabels?.[Math.round(v)] ?? v.toFixed(0);
const ticks = computed(() => {
  const b = bounds.value;
  if (!b) return { x: [], y: [] };
  const n = 4;
  return {
    x: Array.from({ length: n + 1 }, (_, i) => b.x0 + ((b.x1 - b.x0) * i) / n).map((v) => ({ pos: sx(v), text: xName(v) })),
    y: Array.from({ length: n + 1 }, (_, i) => b.y0 + ((b.y1 - b.y0) * i) / n).map((v) => ({ pos: sy(v), text: fmt(v) })),
  };
});

// The crosshair: the x positions every series shares, the nearest one to the pointer.
const xs = computed(() => [...new Set(props.series.flatMap((s) => s.points.map(([x]) => x)))].sort((a, b) => a - b));
const active = ref<number | null>(null);
function onMove(e: PointerEvent) {
  const box = svg.value?.getBoundingClientRect();
  if (!box || !xs.value.length || !bounds.value) return;
  const vx = ((e.clientX - box.left) / box.width) * W.value;
  let best = 0;
  for (let i = 1; i < xs.value.length; i++) if (Math.abs(sx(xs.value[i]) - vx) < Math.abs(sx(xs.value[best]) - vx)) best = i;
  active.value = best;
}
function onKey(e: KeyboardEvent) {
  const n = xs.value.length;
  if (!n) return;
  if (e.key === 'ArrowRight' || e.key === 'ArrowLeft') {
    e.preventDefault();
    const step = e.key === 'ArrowRight' ? 1 : -1;
    active.value = active.value === null ? (step > 0 ? 0 : n - 1) : Math.min(n - 1, Math.max(0, active.value + step));
  } else if (e.key === 'Escape') active.value = null;
}
const tip = computed(() => {
  if (active.value === null) return null;
  const x = xs.value[active.value];
  const left = (sx(x) / W.value) * 100;
  return {
    x,
    pos: sx(x),
    title: xName(x),
    rows: props.series.map((s, i) => {
      const y = s.points.find(([px]) => px === x)?.[1];
      return { label: s.label, slot: slotOf(s, i), value: y !== undefined && Number.isFinite(y) ? fmt(y) : '—', y };
    }),
    style: left > 50 ? { right: `${100 - left + 2}%` } : { left: `${left + 2}%` },
  };
});
</script>

<template>
  <figure class="chart morph">
    <div v-if="bounds" class="plot">
      <svg
        ref="svg"
        :viewBox="`0 0 ${W} ${height}`"
        :height="height"
        role="img"
        :aria-label="series.map((s) => s.label).join(', ')"
        tabindex="0"
        @pointermove="onMove"
        @pointerleave="active = null"
        @keydown="onKey"
        @blur="active = null"
      >
        <g class="grid">
          <line v-for="t in ticks.y" :key="`y${t.pos}`" :x1="PAD.l" :x2="W - PAD.r" :y1="t.pos" :y2="t.pos" />
        </g>
        <g class="labels num">
          <text v-for="t in ticks.y" :key="`yl${t.pos}`" :x="PAD.l - 6" :y="t.pos + 4" text-anchor="end">{{ t.text }}</text>
          <text v-for="t in ticks.x" :key="`xl${t.pos}`" :x="t.pos" :y="height - 8" text-anchor="middle">{{ t.text }}</text>
        </g>
        <path v-for="(s, i) in series" :key="s.label" :d="path(s.points)" class="line" :style="{ stroke: `var(--app-chart-${slotOf(s, i)})`, d: `path('${path(s.points)}')` }" />
        <g v-if="tip">
          <line class="crosshair" :x1="tip.pos" :x2="tip.pos" :y1="PAD.t" :y2="height - PAD.b" />
          <template v-for="r in tip.rows" :key="r.label">
            <circle v-if="r.y !== undefined && Number.isFinite(r.y)" :cx="tip.pos" :cy="sy(r.y)" r="4" class="marker" :style="{ fill: `var(--app-chart-${r.slot})` }" />
          </template>
        </g>
      </svg>
      <div v-if="tip" class="tip type-body-small" :style="tip.style" aria-hidden="true">
        <span class="type-label-large">{{ tip.title }}</span>
        <span v-for="r in tip.rows" :key="r.label" class="tip-row">
          <span class="line-key" :style="{ background: `var(--app-chart-${r.slot})` }" />
          <strong class="num">{{ r.value }}</strong>
          <span class="muted">{{ r.label }}</span>
        </span>
      </div>
    </div>
    <p v-else class="type-body-medium muted empty">{{ emptyText }}</p>
    <figcaption v-if="series.length > 1 || xLabel" class="legend type-label-medium">
      <template v-if="series.length > 1">
        <span v-for="(s, i) in series" :key="s.label" class="key"><span class="swatch" :style="{ background: `var(--app-chart-${slotOf(s, i)})` }" />{{ s.label }}</span>
      </template>
      <span v-if="xLabel" class="muted axis">{{ xLabel }}</span>
    </figcaption>
  </figure>
</template>

<style scoped>
.chart { margin: 0; display: flex; flex-direction: column; gap: var(--app-space-2); min-width: 0; }
.plot { position: relative; min-width: 0; }
svg { display: block; width: 100%; overflow: visible; }
svg:focus-visible { outline-offset: 4px; border-radius: var(--md-sys-shape-corner-small); }
.grid line { stroke: var(--md-sys-color-surface-container-highest); stroke-width: 1; vector-effect: non-scaling-stroke; }
.labels text { fill: var(--md-sys-color-on-surface-variant); font-size: 11px; }
.line { fill: none; stroke-width: 2; stroke-linejoin: round; stroke-linecap: round; vector-effect: non-scaling-stroke; }
/* New data reshapes a line in place where its points keep their count (a refetch, the same range). */
.line { transition: d var(--md-sys-motion-duration-medium4) var(--md-sys-motion-easing-emphasized); }
.crosshair { stroke: var(--md-sys-color-outline); stroke-width: 1; vector-effect: non-scaling-stroke; }
.marker { stroke: var(--app-surface-bg, var(--md-sys-color-surface)); stroke-width: 2; vector-effect: non-scaling-stroke; }
.tip {
  position: absolute; top: 0; z-index: var(--app-z-dropdown); display: flex; flex-direction: column; gap: var(--app-space-1); min-width: 140px; padding: var(--app-space-2) var(--app-space-3);
  border-radius: var(--md-sys-shape-corner-small); background: var(--md-sys-color-inverse-surface); color: var(--md-sys-color-inverse-on-surface); box-shadow: var(--app-elevation-2); pointer-events: none;
}
.tip .muted { color: inherit; opacity: 0.8; }
.tip-row { display: grid; grid-template-columns: 12px auto 1fr; align-items: center; gap: var(--app-space-2); }
.line-key { width: 12px; height: 2px; border-radius: 1px; }
.legend { display: flex; flex-wrap: wrap; gap: var(--app-space-3); align-items: center; }
.key { display: inline-flex; align-items: center; gap: var(--app-space-1); }
.swatch { width: 12px; height: 2px; border-radius: 1px; }
.axis { margin-left: auto; }
.empty { padding: var(--app-space-6); text-align: center; }
</style>
