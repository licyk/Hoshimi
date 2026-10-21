<script setup lang="ts" generic="T extends string">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch, type Component } from 'vue';
import AppIcon from '@/ui/AppIcon.vue';
import SelectField from '@/ui/SelectField.vue';

export interface SegmentOption<V extends string = string> {
  value: V;
  label: string;
  icon?: Component;
  /** A number shown beside the label (how many items the filter keeps). */
  count?: number | null;
}

/**
 * One choice out of a few, as a rounded track with a pill that slides to the chosen option: the
 * filter over a list, the range of a report. The pill moves and stretches between options over the
 * emphasized curve, so the eye follows the change rather than finding it; counts ride along in
 * small badges. Arrow keys, Home and End move the choice (one tab stop for the group).
 *
 * Where the options do not fit their row (a phone), the control becomes a select of the same
 * options instead of wrapping or scrolling sideways.
 */
const props = defineProps<{ options: SegmentOption<T>[]; label: string }>();
const model = defineModel<T>({ required: true });

const root = ref<HTMLElement | null>(null);
const track = ref<HTMLElement | null>(null);
// By index, in the options' order (a v-for ref array does not promise that order).
const buttons: (HTMLButtonElement | null)[] = [];
const pill = ref<{ x: number; w: number } | null>(null);
// The pill appears in place on the first measure, and glides only for a change after that.
const ready = ref(false);
const collapsed = ref(false);
let natural = 0;

const index = computed(() => props.options.findIndex((o) => o.value === model.value));

function measure() {
  const el = track.value;
  const box = root.value;
  if (!box) return;
  if (el) natural = el.scrollWidth;
  const fits = !natural || natural <= box.clientWidth + 1;
  if (collapsed.value === fits) {
    collapsed.value = !fits;
    if (!collapsed.value) void nextTick(measure);
    return;
  }
  const b = buttons[index.value];
  if (!el || !b) return (pill.value = null);
  pill.value = { x: b.offsetLeft, w: b.offsetWidth };
  if (!ready.value) requestAnimationFrame(() => (ready.value = true));
}

let observer: ResizeObserver | null = null;
onMounted(() => {
  measure();
  if (typeof ResizeObserver === 'undefined' || !root.value) return;
  observer = new ResizeObserver(() => measure());
  observer.observe(root.value);
});
onBeforeUnmount(() => observer?.disconnect());
// A count changing width (9 → 10) moves the options after it; the pill follows.
watch([index, () => props.options.map((o) => `${o.label}${o.count ?? ''}`).join()], () => void nextTick(measure), { flush: 'post' });

function choose(i: number) {
  const o = props.options[i];
  if (!o) return;
  model.value = o.value;
  void nextTick(() => buttons[i]?.focus());
}
function onKey(e: KeyboardEvent) {
  const n = props.options.length;
  const at = Math.max(0, index.value);
  const next = { ArrowRight: at + 1, ArrowDown: at + 1, ArrowLeft: at - 1, ArrowUp: at - 1, Home: 0, End: n - 1 }[e.key];
  if (next === undefined) return;
  e.preventDefault();
  choose((next + n) % n);
}
const selectOptions = computed(() => props.options.map((o) => ({ value: o.value, label: o.count === null || o.count === undefined ? o.label : `${o.label} (${o.count})` })));
const selectModel = computed<string | null>({ get: () => model.value, set: (v) => v && (model.value = v as T) });
</script>

<template>
  <div ref="root" class="segmented-control" :class="{ collapsed }">
    <SelectField v-if="collapsed" v-model="selectModel" class="select" :label="label" :options="selectOptions" />
    <div v-else ref="track" class="track" :class="{ ready }" role="radiogroup" :aria-label="label" @keydown="onKey">
      <span v-if="pill" class="pill" aria-hidden="true" :style="{ transform: `translateX(${pill.x}px)`, width: `${pill.w}px` }" />
      <button
        v-for="(o, i) in options"
        :key="o.value"
        :ref="(el) => (buttons[i] = el as HTMLButtonElement)"
        type="button"
        role="radio"
        class="option type-label-large"
        :class="{ selected: i === index }"
        :aria-checked="i === index"
        :tabindex="i === index || (index < 0 && i === 0) ? 0 : -1"
        @click="choose(i)"
      >
        <AppIcon v-if="o.icon" :icon="o.icon" :size="18" />
        <span class="label">{{ o.label }}</span>
        <span v-if="o.count !== null && o.count !== undefined" class="count num type-label-medium">{{ o.count }}</span>
      </button>
    </div>
  </div>
</template>

<style scoped>
.segmented-control { min-width: 0; max-width: 100%; }
.segmented-control.collapsed { align-self: stretch; width: 100%; }
.select { width: 100%; }
.track {
  position: relative; isolation: isolate; display: inline-flex; align-items: center; gap: 2px; height: 40px; padding: 4px;
  border-radius: var(--md-sys-shape-corner-full); background: var(--md-sys-color-surface-container-high); white-space: nowrap;
}
.pill {
  position: absolute; z-index: -1; top: 4px; bottom: 4px; left: 0; border-radius: var(--md-sys-shape-corner-full);
  background: var(--md-sys-color-secondary-container); box-shadow: var(--app-elevation-1);
}
.ready .pill {
  transition:
    transform var(--md-sys-motion-duration-medium2) var(--md-sys-motion-easing-emphasized),
    width var(--md-sys-motion-duration-medium2) var(--md-sys-motion-easing-emphasized);
}
.option {
  position: relative; display: inline-flex; align-items: center; justify-content: center; gap: var(--app-space-2); height: 100%; min-width: 48px;
  padding: 0 var(--app-space-4); border: 0; border-radius: var(--md-sys-shape-corner-full); background: transparent; color: var(--md-sys-color-on-surface-variant);
  font: inherit; cursor: pointer; transition: color var(--md-sys-motion-duration-short4) var(--md-sys-motion-easing-standard);
}
.option::before {
  content: ''; position: absolute; inset: 0; border-radius: inherit; background: currentColor; opacity: 0; pointer-events: none;
  transition: opacity var(--md-sys-motion-duration-short2) var(--md-sys-motion-easing-standard);
}
.option:hover:not(.selected)::before { opacity: var(--md-sys-state-hover-state-layer-opacity); }
.option:active::before { opacity: var(--md-sys-state-pressed-state-layer-opacity); }
.option:focus-visible { outline-offset: -2px; }
.option.selected { color: var(--md-sys-color-on-secondary-container); }
.count {
  min-width: 20px; padding: 0 6px; border-radius: var(--md-sys-shape-corner-full); text-align: center; line-height: 18px;
  background: color-mix(in srgb, currentColor 10%, transparent);
}
@media (forced-colors: active) {
  .option.selected { outline: 2px solid Highlight; }
}
</style>
