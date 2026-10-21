<script setup lang="ts">
import { computed } from 'vue';

export interface MeterSegment {
  label: string;
  value: number;
  /** A chart slot (1-8, ``--app-chart-n``), so a segment keeps the colour its series has in the charts. */
  slot: number;
}

/**
 * A thin horizontal bar. With ``segments`` it shows a whole split into parts (input against cache
 * reads and writes), each in its series colour with a 2px surface gap between them; with ``value``
 * (0..1) it is a level whose fill carries the tone (an allowance running out turns warning, then
 * error). Its numbers are always also said in text beside it or in ``label``.
 */
const props = withDefaults(defineProps<{ segments?: MeterSegment[]; value?: number | null; tone?: 'primary' | 'warning' | 'error'; label: string; height?: number }>(), {
  segments: undefined,
  value: null,
  tone: 'primary',
  height: 6,
});
const total = computed(() => (props.segments ?? []).reduce((sum, s) => sum + Math.max(0, s.value), 0));
const parts = computed(() => (props.segments ?? []).filter((s) => s.value > 0).map((s) => ({ ...s, share: s.value / (total.value || 1) })));
const level = computed(() => Math.min(1, Math.max(0, props.value ?? 0)));
</script>

<template>
  <div class="meter" :class="tone" :style="{ '--h': `${height}px` }" role="img" :aria-label="label">
    <template v-if="segments">
      <span v-for="p in parts" :key="p.label" class="part" :style="{ flexGrow: p.share, background: `var(--app-chart-${p.slot})` }" :title="p.label" />
    </template>
    <span v-else class="fill" :style="{ width: `${level * 100}%` }" />
  </div>
</template>

<style scoped>
.meter { display: flex; gap: 2px; height: var(--h); min-width: 0; border-radius: var(--md-sys-shape-corner-full); overflow: hidden; background: var(--md-sys-color-surface-container-highest); }
.part { flex-basis: 0; min-width: 2px; transition: flex-grow var(--md-sys-motion-duration-medium2) var(--md-sys-motion-easing-emphasized); }
.fill { border-radius: inherit; background: var(--md-sys-color-primary); transition: width var(--md-sys-motion-duration-medium2) var(--md-sys-motion-easing-emphasized), background-color var(--md-sys-motion-duration-short4) var(--md-sys-motion-easing-standard); }
/* The unfilled track is a lighter step of the fill's own family, so state reads across the whole bar. */
.warning { background: var(--md-sys-color-tertiary-container); }
.warning .fill { background: var(--md-sys-color-tertiary); }
.error { background: var(--md-sys-color-error-container); }
.error .fill { background: var(--md-sys-color-error); }
</style>
