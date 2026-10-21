<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue';
import AppIcon from '@/ui/AppIcon.vue';
import { ChevronLeft, ChevronRight } from '@/ui/icons';
import { useLayer } from '@/ui/layers';
import { TRANSITIONS } from '@/ui/motion/transitions';

/**
 * A month of days to pick one from, anchored to its trigger (the default slot, given ``toggle``):
 * the day a report shows. Days outside ``min``..``max`` cannot be chosen; ``marks`` shades a day by
 * how much it held (0 to 1), so the busy days show before they are opened. Months slide the way
 * they are paged; arrow keys move by a day or a week, Page Up and Down by a month, Enter picks.
 * Dates are local calendar days, ``YYYY-MM-DD``.
 */
const props = withDefaults(
  defineProps<{ min?: string | null; max: string; marks?: Record<string, number>; label: string; todayLabel: string; previousLabel: string; nextLabel: string; locale?: string }>(),
  { min: null, marks: () => ({}), locale: undefined },
);
const model = defineModel<string>({ required: true });
const open = ref(false);
const root = ref<HTMLElement | null>(null);
const panel = ref<HTMLElement | null>(null);
const position = ref<Record<string, string>>({});
const layer = useLayer({
  kind: 'menu',
  open: () => open.value,
  close: () => (open.value = false),
  elements: () => [root.value, panel.value],
  initialFocus: () => panel.value?.querySelector<HTMLButtonElement>('.day[tabindex="0"]'),
});

const pad = (n: number) => String(n).padStart(2, '0');
const iso = (d: Date) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
const parse = (s: string) => {
  const [y, m, d] = s.split('-').map(Number);
  return new Date(y, (m || 1) - 1, d || 1);
};
const allowed = (day: string) => day <= props.max && (!props.min || day >= props.min);

// The month on show, and the day the keyboard is on (the chosen day when it opens).
const month = ref(parse(model.value));
const focus = ref(model.value);
const dir = ref(1);
const monthKey = computed(() => `${month.value.getFullYear()}-${pad(month.value.getMonth() + 1)}`);
const monthTitle = computed(() => month.value.toLocaleDateString(props.locale, { year: 'numeric', month: 'long' }));
// Weeks start on the locale's first day where the browser says it (Intl.Locale weekInfo), else Monday.
const firstDay = computed(() => {
  try {
    const info = (new Intl.Locale(props.locale ?? navigator.language) as unknown as { weekInfo?: { firstDay: number }; getWeekInfo?: () => { firstDay: number } });
    return ((info.getWeekInfo?.() ?? info.weekInfo)?.firstDay ?? 1) % 7;
  } catch {
    return 1;
  }
});
const weekdays = computed(() => Array.from({ length: 7 }, (_, i) => new Date(2024, 0, 7 + ((firstDay.value + i) % 7)).toLocaleDateString(props.locale, { weekday: 'narrow' })));
const cells = computed(() => {
  const first = new Date(month.value.getFullYear(), month.value.getMonth(), 1);
  const lead = (first.getDay() - firstDay.value + 7) % 7;
  const days = new Date(month.value.getFullYear(), month.value.getMonth() + 1, 0).getDate();
  return Array.from({ length: Math.ceil((lead + days) / 7) * 7 }, (_, i) => {
    const n = i - lead + 1;
    if (n < 1 || n > days) return null;
    const date = iso(new Date(first.getFullYear(), first.getMonth(), n));
    return { date, n, allowed: allowed(date), mark: props.marks[date] ?? 0 };
  });
});
const canPrevious = computed(() => !props.min || iso(new Date(month.value.getFullYear(), month.value.getMonth(), 0)) >= props.min);
const canNext = computed(() => iso(new Date(month.value.getFullYear(), month.value.getMonth() + 1, 1)) <= props.max);

function showMonth(offset: number) {
  dir.value = offset > 0 ? 1 : -1;
  month.value = new Date(month.value.getFullYear(), month.value.getMonth() + offset, 1);
}
function moveFocus(days: number, months = 0) {
  const d = parse(focus.value);
  const next = months ? new Date(d.getFullYear(), d.getMonth() + months, Math.min(d.getDate(), 28)) : new Date(d.getFullYear(), d.getMonth(), d.getDate() + days);
  let target = iso(next);
  if (target > props.max) target = props.max;
  if (props.min && target < props.min) target = props.min;
  focus.value = target;
  const t = parse(target);
  if (t.getFullYear() !== month.value.getFullYear() || t.getMonth() !== month.value.getMonth()) {
    dir.value = target > iso(month.value) ? 1 : -1;
    month.value = new Date(t.getFullYear(), t.getMonth(), 1);
  }
  void nextTick(() => panel.value?.querySelector<HTMLButtonElement>(`[data-date="${target}"]`)?.focus());
}
function pick(day: string) {
  if (!allowed(day)) return;
  model.value = day;
  open.value = false;
}
function onKey(e: KeyboardEvent) {
  const moves: Record<string, [number, number]> = { ArrowLeft: [-1, 0], ArrowRight: [1, 0], ArrowUp: [-7, 0], ArrowDown: [7, 0], PageUp: [0, -1], PageDown: [0, 1] };
  const move = moves[e.key];
  if (move) {
    e.preventDefault();
    moveFocus(...move);
  } else if (e.key === 'Enter' || e.key === ' ') {
    e.preventDefault();
    pick(focus.value);
  }
}

const MARGIN = 8;
function place() {
  const trigger = root.value?.firstElementChild ?? root.value;
  if (!trigger) return;
  const anchor = trigger.getBoundingClientRect();
  const box = panel.value?.getBoundingClientRect();
  const width = box?.width ?? 320;
  const height = box?.height ?? 360;
  const below = window.innerHeight - anchor.bottom;
  const openUp = height > below - MARGIN && anchor.top > below;
  position.value = {
    left: `${Math.min(Math.max(MARGIN, anchor.left), Math.max(MARGIN, window.innerWidth - width - MARGIN))}px`,
    top: openUp ? '' : `${anchor.bottom + 4}px`,
    bottom: openUp ? `${window.innerHeight - anchor.top + 4}px` : '',
  };
}
const onReflow = (e?: Event) => {
  if (open.value && !(e?.type === 'scroll' && panel.value?.contains(e.target as Node))) place();
};
watch(open, async (v) => {
  if (v) {
    month.value = parse(model.value);
    focus.value = model.value;
    window.addEventListener('scroll', onReflow, true);
    window.addEventListener('resize', onReflow);
    await nextTick();
    place();
  } else {
    window.removeEventListener('scroll', onReflow, true);
    window.removeEventListener('resize', onReflow);
  }
});
onBeforeUnmount(() => {
  open.value = false;
  window.removeEventListener('scroll', onReflow, true);
  window.removeEventListener('resize', onReflow);
});
const toggle = () => (open.value = !open.value);
</script>

<template>
  <div ref="root" class="date-root" @click.stop>
    <slot :toggle="toggle" :open="open" />
    <Teleport to="body">
      <Transition name="snackbar">
        <div v-if="open" ref="panel" class="calendar" role="dialog" :aria-label="label" :style="{ ...position, zIndex: layer.zIndex.value }" @click.stop @keydown="onKey">
          <div class="head">
            <button type="button" class="nav state-layer" :aria-label="previousLabel" :disabled="!canPrevious" @click="showMonth(-1)"><AppIcon :icon="ChevronLeft" :size="20" /></button>
            <span class="type-title-small title" aria-live="polite">{{ monthTitle }}</span>
            <button type="button" class="nav state-layer" :aria-label="nextLabel" :disabled="!canNext" @click="showMonth(1)"><AppIcon :icon="ChevronRight" :size="20" /></button>
          </div>
          <div class="weekdays type-label-small" aria-hidden="true"><span v-for="(w, i) in weekdays" :key="i">{{ w }}</span></div>
          <div class="months" :style="{ '--axis-dir': dir }">
            <Transition :name="TRANSITIONS.sharedAxisX">
              <div :key="monthKey" class="grid" role="grid">
                <template v-for="(c, i) in cells" :key="c?.date ?? `pad${i}`">
                  <span v-if="!c" />
                  <button
                    v-else
                    type="button"
                    role="gridcell"
                    class="day type-body-medium num"
                    :class="{ chosen: c.date === model, today: c.date === max, marked: c.mark > 0 }"
                    :style="c.mark > 0 ? { '--mark': `${Math.round(12 + c.mark * 40)}%` } : undefined"
                    :data-date="c.date"
                    :disabled="!c.allowed"
                    :tabindex="c.date === focus ? 0 : -1"
                    :aria-selected="c.date === model"
                    @click="pick(c.date)"
                  >
                    {{ c.n }}
                  </button>
                </template>
              </div>
            </Transition>
          </div>
          <div class="foot">
            <button type="button" class="today-button state-layer type-label-large" @click="pick(max)">{{ todayLabel }}</button>
          </div>
        </div>
      </Transition>
    </Teleport>
  </div>
</template>

<style scoped>
.date-root { display: inline-flex; }
.calendar {
  position: fixed; width: 304px; padding: var(--app-space-3); display: flex; flex-direction: column; gap: var(--app-space-2);
  background: var(--md-sys-color-surface-container-high); color: var(--md-sys-color-on-surface); border-radius: var(--md-sys-shape-corner-large); box-shadow: var(--app-elevation-2);
}
.head { display: flex; align-items: center; gap: var(--app-space-1); }
.title { flex: 1; text-align: center; }
.nav, .today-button { border: 0; background: transparent; color: var(--md-sys-color-on-surface-variant); cursor: pointer; font: inherit; }
.nav { display: grid; place-items: center; width: 40px; height: 40px; border-radius: 50%; }
.nav:disabled { opacity: var(--app-disabled-content-opacity); cursor: default; }
.weekdays, .grid { display: grid; grid-template-columns: repeat(7, 1fr); gap: 2px; }
.weekdays span { text-align: center; color: var(--md-sys-color-on-surface-variant); }
.months { position: relative; overflow-x: clip; }
.day {
  position: relative; display: grid; place-items: center; height: 36px; border: 0; border-radius: 50%; padding: 0; cursor: pointer; font: inherit;
  background: transparent; color: var(--md-sys-color-on-surface);
  transition: background-color var(--md-sys-motion-duration-short3) var(--md-sys-motion-easing-standard);
}
/* How much a day held, as a wash of the primary tone behind its number. */
.day.marked { background: color-mix(in srgb, var(--md-sys-color-primary) var(--mark), transparent); }
.day:hover:not(:disabled):not(.chosen) { outline: 1px solid var(--md-sys-color-outline-variant); outline-offset: -1px; }
.day.today { font-weight: 700; color: var(--md-sys-color-primary); }
.day.chosen { background: var(--md-sys-color-primary); color: var(--md-sys-color-on-primary); }
.day:disabled { color: var(--md-sys-color-on-surface); opacity: var(--app-disabled-content-opacity); cursor: default; }
.day:focus-visible { outline-offset: 1px; }
.foot { display: flex; justify-content: flex-end; }
.today-button { height: 40px; padding: 0 var(--app-space-3); border-radius: var(--md-sys-shape-corner-full); color: var(--md-sys-color-primary); }
</style>
