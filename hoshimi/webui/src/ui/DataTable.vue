<script setup lang="ts" generic="T">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue';
import AppIcon from '@/ui/AppIcon.vue';
import { ArrowDown, ArrowUp } from '@/ui/icons';
import ScrollArea from '@/ui/ScrollArea.vue';
import { useElementWidth } from '@/ui/useElementWidth';

export interface Column {
  key: string;
  label: string;
  /** Numbers align to the end, so their digits line up (tabular figures). */
  align?: 'start' | 'end';
  /**
   * The grid track; ``minmax(0, 1fr)`` when not given. Never content-sized (``auto``): the header
   * and each row are grids of their own, so a track sized by content would differ from row to row.
   */
  width?: string;
  sortable?: boolean;
  /** Below this table width the column is left out, least important first, so a narrow pane keeps what matters. */
  minWidth?: number;
}
export interface SortState {
  key: string;
  dir: 'asc' | 'desc';
}

/**
 * Rows of one kind in one grid: the header and every row share the column tracks, so values line
 * up whatever their length; a long name is cut with an ellipsis (its cell slot can add a tooltip),
 * never pushing the columns after it. The table scrolls inside itself (ScrollArea) with its header
 * held at the top, and past ``WINDOW_FROM`` rows renders only those in view, so a long history
 * stays smooth. Rows are a fixed height for that reason (``rowHeight``).
 */
const props = withDefaults(
  defineProps<{
    columns: Column[];
    rows: T[];
    rowKey: (row: T) => string;
    /** The value a column sorts and shows by default; ``row[key]`` when not given. */
    value?: (row: T, key: string) => unknown;
    label: string;
    maxHeight?: string;
    rowHeight?: number;
    clickable?: boolean;
    /** The row shown as chosen (its detail open beside or over the table). */
    selected?: string | null;
    /** Rows are being refetched: the table keeps its frame, dimmed, rather than flashing. */
    busy?: boolean;
    emptyText?: string;
  }>(),
  { value: undefined, maxHeight: undefined, rowHeight: 52, clickable: false, selected: null, busy: false, emptyText: '' },
);
const sort = defineModel<SortState | null>('sort', { default: null });
const emit = defineEmits<{ select: [row: T]; end: [] }>();

const WINDOW_FROM = 200;
const OVERSCAN = 8;
const root = ref<HTMLElement | null>(null);
const width = useElementWidth(root, 1024);
const visible = computed(() => props.columns.filter((c) => !c.minWidth || width.value >= c.minWidth));
const template = computed(() => visible.value.map((c) => c.width ?? 'minmax(0, 1fr)').join(' '));
const read = (row: T, key: string): unknown => (props.value ? props.value(row, key) : (row as Record<string, unknown>)[key]);

const sorted = computed(() => {
  const s = sort.value;
  if (!s) return props.rows;
  const factor = s.dir === 'asc' ? 1 : -1;
  return [...props.rows].sort((a, b) => {
    const x = read(a, s.key);
    const y = read(b, s.key);
    // Empty values (no measurement) sort last either way.
    if (x === null || x === undefined) return y === null || y === undefined ? 0 : 1;
    if (y === null || y === undefined) return -1;
    if (typeof x === 'number' && typeof y === 'number') return (x - y) * factor;
    return String(x).localeCompare(String(y)) * factor;
  });
});
function toggle(column: Column) {
  if (!column.sortable) return;
  const s = sort.value;
  // A number's first click shows the largest first; a name's, A to Z.
  const first = column.align === 'end' ? 'desc' : 'asc';
  sort.value = s?.key !== column.key ? { key: column.key, dir: first } : { key: column.key, dir: s.dir === 'asc' ? 'desc' : 'asc' };
}
const ariaSort = (column: Column) => (sort.value?.key === column.key ? (sort.value.dir === 'asc' ? 'ascending' : 'descending') : column.sortable ? 'none' : undefined);

// Windowing: the scroller's offset picks the slice; spacers keep the scrollbar honest.
const scroller = ref<InstanceType<typeof ScrollArea> | null>(null);
const scrollTop = ref(0);
const viewport = ref(600);
const windowed = computed(() => sorted.value.length > WINDOW_FROM);
const range = computed(() => {
  if (!windowed.value) return { from: 0, to: sorted.value.length };
  const from = Math.max(0, Math.floor(scrollTop.value / props.rowHeight) - OVERSCAN);
  const to = Math.min(sorted.value.length, Math.ceil((scrollTop.value + viewport.value) / props.rowHeight) + OVERSCAN);
  return { from, to };
});
const shown = computed(() => sorted.value.slice(range.value.from, range.value.to));
function onScroll() {
  const box = scroller.value?.box;
  if (!box) return;
  scrollTop.value = box.scrollTop;
  viewport.value = box.clientHeight || viewport.value;
}
onMounted(() => scroller.value?.box?.addEventListener('scroll', onScroll, { passive: true }));
onBeforeUnmount(() => scroller.value?.box?.removeEventListener('scroll', onScroll));
// A new sort or a filter starts the list from its top, where the reader looks for the change.
watch(sort, () => scroller.value?.box?.scrollTo({ top: 0 }));
</script>

<template>
  <div ref="root" class="data-table" :class="{ busy }" role="table" :aria-label="label" :aria-rowcount="rows.length + 1" :style="{ '--cols': template, '--row-h': `${rowHeight}px` }">
    <ScrollArea ref="scroller" :max-height="maxHeight" @end="emit('end')">
      <template #header>
        <div class="row head" role="row">
          <div v-for="c in visible" :key="c.key" class="cell type-label-medium" :class="[c.align ?? 'start']" role="columnheader" :aria-sort="ariaSort(c)">
            <button v-if="c.sortable" type="button" class="sort state-layer" @click="toggle(c)">
              <span class="label">{{ c.label }}</span>
              <AppIcon v-if="sort?.key === c.key" :icon="sort.dir === 'asc' ? ArrowUp : ArrowDown" :size="18" />
            </button>
            <span v-else class="label">{{ c.label }}</span>
          </div>
        </div>
      </template>
      <div v-if="!rows.length" class="empty type-body-medium muted"><slot name="empty">{{ emptyText }}</slot></div>
      <div v-else role="rowgroup" :style="windowed ? { paddingTop: `${range.from * rowHeight}px`, paddingBottom: `${(sorted.length - range.to) * rowHeight}px` } : undefined">
        <div
          v-for="row in shown"
          :key="rowKey(row)"
          class="row body"
          :class="{ clickable, selected: selected !== null && selected === rowKey(row) }"
          role="row"
          :tabindex="clickable ? 0 : undefined"
          :aria-selected="clickable ? selected === rowKey(row) : undefined"
          @click="clickable && emit('select', row)"
          @keydown.enter.prevent="clickable && emit('select', row)"
          @keydown.space.prevent="clickable && emit('select', row)"
        >
          <div v-for="c in visible" :key="c.key" class="cell type-body-medium" :class="[c.align ?? 'start', { num: c.align === 'end' }]" role="cell">
            <slot :name="`cell-${c.key}`" :row="row" :value="read(row, c.key)">
              <span class="text">{{ read(row, c.key) ?? '—' }}</span>
            </slot>
          </div>
        </div>
      </div>
      <slot name="footer" />
    </ScrollArea>
  </div>
</template>

<style scoped>
.data-table { min-width: 0; transition: opacity var(--md-sys-motion-duration-short4) var(--md-sys-motion-easing-standard); }
.data-table.busy { opacity: 0.6; }
.row { display: grid; grid-template-columns: var(--cols); gap: var(--app-space-3); align-items: center; padding: 0 var(--app-space-3); }
.head { min-height: 40px; color: var(--md-sys-color-on-surface-variant); border-bottom: 1px solid var(--md-sys-color-outline-variant); }
.body { height: var(--row-h); border-radius: var(--md-sys-shape-corner-small); }
.body + .body { box-shadow: inset 0 1px 0 var(--md-sys-color-surface-container-high); }
.clickable { cursor: pointer; position: relative; isolation: isolate; }
.clickable::before {
  content: ''; position: absolute; inset: 0; border-radius: inherit; background: var(--md-sys-color-on-surface); opacity: 0; z-index: -1;
  transition: opacity var(--md-sys-motion-duration-short2) var(--md-sys-motion-easing-standard);
}
.clickable:hover::before { opacity: var(--md-sys-state-hover-state-layer-opacity); }
.clickable:focus-visible { outline-offset: -2px; }
.selected { background: var(--md-sys-color-secondary-container); color: var(--md-sys-color-on-secondary-container); box-shadow: none; }
.cell { display: flex; align-items: center; gap: var(--app-space-2); min-width: 0; }
.cell.end { justify-content: flex-end; text-align: end; }
.text, .label { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.sort {
  display: inline-flex; align-items: center; gap: var(--app-space-1); min-width: 0; max-width: 100%; padding: var(--app-space-1) var(--app-space-1);
  margin: 0 calc(-1 * var(--app-space-1)); border: 0; border-radius: var(--md-sys-shape-corner-small); background: none; color: inherit; font: inherit; cursor: pointer;
}
.empty { padding: var(--app-space-6) var(--app-space-3); text-align: center; }
</style>
