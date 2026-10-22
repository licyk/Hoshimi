<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, ref, watch, type Component } from 'vue';
import AppIcon from '@/ui/AppIcon.vue';
import ChipButton from '@/ui/ChipButton.vue';
import { Check, Search } from '@/ui/icons';
import { useLayer } from '@/ui/layers';
import { TRANSITIONS } from '@/ui/motion/transitions';

export interface ComposerOption {
  value: string;
  label: string;
  /** A second line under the name: a model's display name and provider. */
  description?: string;
  /** More words the search matches. */
  keywords?: string[];
  /** Shown, but not to be picked (a tool turned off in Settings). */
  disabled?: boolean;
}

/**
 * A setting that lives in a composer's tool row (the model, the effort): a ``ChipButton`` showing
 * the choice, opening a menu over it — or under it, when there is more room there. The menu has a
 * heading, a search for a long list and a check on the current choice; the arrows move through it
 * and Enter picks. It is rendered at the end of the document, so the composer never clips it.
 *
 * It lines up with its control (``align="start"``), or stands centred over the composer the control
 * is in (``align="center"``: a long list to choose from, centred on a phone's screen); either way it
 * is kept inside the window. A menu with a search keeps one size whatever the search leaves in it,
 * so it does not shrink and jump as the list empties.
 *
 * With ``multiple``, it picks any number (``v-model:values``): a press checks or unchecks an
 * option, and the menu stays open.
 */
const props = withDefaults(
  defineProps<{
    /** The menu's heading and the control's accessible name. */
    label: string;
    options: ComposerOption[];
    icon?: Component;
    /** A line under the heading saying what the setting does. */
    hint?: string;
    /** What the control shows; the chosen option's label by default. */
    text?: string;
    placeholder?: string;
    disabled?: boolean;
    loading?: boolean;
    /** The search field's label; without it there is no search. */
    searchLabel?: string;
    /** The search shows from this many options. */
    searchFrom?: number;
    noMatches?: string;
    /** Where the menu stands: lined up with its control, or centred over the composer it is in. */
    align?: 'start' | 'center';
    multiple?: boolean;
  }>(),
  { searchFrom: 0, noMatches: '', align: 'start', multiple: false },
);
const model = defineModel<string | null>({ default: null });
const values = defineModel<string[]>('values', { default: () => [] });
const selected = (o: ComposerOption) => (props.multiple ? values.value.includes(o.value) : o.value === model.value);

const open = ref(false);
const root = ref<HTMLElement | null>(null);
const menu = ref<HTMLElement | null>(null);
const search = ref<HTMLInputElement | null>(null);
const list = ref<HTMLElement | null>(null);
const query = ref('');
const active = ref(-1);
const position = ref<Record<string, string>>({});

const current = computed(() => props.options.find((o) => o.value === model.value) ?? null);
const display = computed(() => props.text ?? current.value?.label ?? props.placeholder ?? '');
const searchable = computed(() => !!props.searchLabel && props.options.length >= props.searchFrom);
const shown = computed(() => {
  const q = query.value.trim().toLowerCase();
  if (!q || !searchable.value) return props.options;
  return props.options.filter((o) => [o.label, o.description ?? '', ...(o.keywords ?? [])].some((s) => s.toLowerCase().includes(q)));
});

const layer = useLayer({
  kind: 'menu',
  open: () => open.value,
  close: () => (open.value = false),
  elements: () => [root.value, menu.value],
  initialFocus: () => (searchable.value ? search.value : itemAt(active.value)),
});

const GAP = 6;
const MARGIN = 8;

function place() {
  const anchor = root.value?.getBoundingClientRect();
  const el = menu.value;
  if (!anchor || !el) return;
  // The menu's own layout size, not its box on screen: it opens scaled down, and a box measured
  // mid-way through that is narrower than the menu will be, which then runs off the window.
  const box = { width: el.offsetWidth, height: el.offsetHeight };
  const below = window.innerHeight - anchor.bottom - MARGIN;
  const above = anchor.top - MARGIN;
  // Over the control (it sits at the foot of the page), unless the menu fits only under it.
  const up = box.height <= above - GAP || above >= below;
  const frame = props.align === 'center' ? (root.value?.closest('.composer')?.getBoundingClientRect() ?? null) : null;
  const wanted = frame ? frame.left + (frame.width - box.width) / 2 : anchor.left;
  const left = Math.min(Math.max(MARGIN, wanted), Math.max(MARGIN, window.innerWidth - box.width - MARGIN));
  position.value = {
    left: `${left}px`,
    top: up ? '' : `${anchor.bottom + GAP}px`,
    bottom: up ? `${window.innerHeight - anchor.top + GAP}px` : '',
    maxHeight: `${Math.max(160, (up ? above : below) - GAP)}px`,
    '--popover-origin': `${up ? 'bottom' : 'top'} ${anchor.left - left + anchor.width / 2}px`,
    '--popover-shift': up ? '6px' : '-6px',
  };
}

const itemAt = (i: number) => list.value?.querySelectorAll<HTMLButtonElement>('[role="option"]')[i] ?? null;

function highlight(i: number, focus: boolean) {
  active.value = i;
  const item = itemAt(i);
  item?.scrollIntoView({ block: 'nearest' });
  if (focus) item?.focus();
}

function onKey(e: KeyboardEvent) {
  if (!layer.isTop() || !shown.value.length) return;
  const count = shown.value.length;
  const step = { ArrowDown: 1, ArrowUp: -1 }[e.key];
  if (step) {
    e.preventDefault();
    // The search keeps the focus while the arrows move the highlight; without one the focus moves.
    highlight((active.value + step + count) % count, !searchable.value);
  } else if (e.key === 'Home' || e.key === 'End') {
    if (searchable.value && document.activeElement === search.value) return;
    e.preventDefault();
    highlight(e.key === 'Home' ? 0 : count - 1, !searchable.value);
  } else if (e.key === 'Enter' && document.activeElement === search.value && shown.value[active.value]) {
    e.preventDefault();
    pick(shown.value[active.value]);
  }
}
const onReflow = (e?: Event) => {
  if (!open.value || (e?.type === 'scroll' && menu.value?.contains(e.target as Node))) return;
  place();
};
function listen(on: boolean) {
  const method = on ? 'addEventListener' : 'removeEventListener';
  window[method]('scroll', onReflow, true);
  window[method]('resize', onReflow);
}

watch(open, async (isOpen) => {
  listen(isOpen);
  if (!isOpen) return;
  query.value = '';
  active.value = props.multiple ? 0 : Math.max(0, props.options.findIndex((o) => o.value === model.value));
  await nextTick();
  place();
  itemAt(active.value)?.scrollIntoView({ block: 'nearest' });
});
// A search starts the highlight over at its first match; what is left may need another place.
watch(query, () => (active.value = 0));
watch(shown, () => open.value && void nextTick(place));
// The control can go away while its menu is open (no more effort levels for the new model).
watch(() => props.disabled, (disabled) => disabled && (open.value = false));
onBeforeUnmount(() => {
  open.value = false;
  listen(false);
});

function toggle() {
  if (!props.loading) open.value = !open.value;
}
function pick(o: ComposerOption) {
  if (o.disabled) return;
  if (props.multiple) {
    values.value = selected(o) ? values.value.filter((v) => v !== o.value) : [...values.value, o.value];
    return;
  }
  model.value = o.value;
  open.value = false;
}
</script>

<template>
  <div ref="root" class="composer-menu">
    <ChipButton :icon="icon" :text="display" :label="text || current ? `${label}: ${display}` : label" menu :open="open" :disabled="disabled" :loading="loading" @click="toggle" />
    <Teleport to="body">
      <Transition :name="TRANSITIONS.popover">
        <div v-if="open" ref="menu" class="menu" :class="{ fixed: searchable }" role="dialog" :aria-label="label" :style="{ ...position, zIndex: layer.zIndex.value }" @keydown="onKey">
          <header class="head">
            <span class="type-title-small">{{ label }}</span>
            <span v-if="hint" class="type-body-small muted">{{ hint }}</span>
          </header>
          <label v-if="searchable" class="search">
            <AppIcon :icon="Search" :size="18" class="muted" />
            <input ref="search" v-model="query" type="text" role="searchbox" class="type-body-medium" :placeholder="searchLabel" :aria-label="searchLabel" autocomplete="off" spellcheck="false" />
          </label>
          <div ref="list" class="list app-scroll app-scroll-auto" role="listbox" :aria-label="label" :aria-multiselectable="multiple || undefined">
            <button
              v-for="(o, i) in shown"
              :key="o.value"
              type="button"
              role="option"
              class="item state-layer"
              :class="{ active: i === active }"
              :aria-selected="selected(o)"
              :aria-disabled="o.disabled || undefined"
              :tabindex="searchable ? -1 : 0"
              @click="pick(o)"
              @pointermove="active = i"
            >
              <span class="text">
                <span class="type-body-medium name">{{ o.label }}</span>
                <span v-if="o.description" class="type-body-small muted meta">{{ o.description }}</span>
              </span>
              <AppIcon v-if="selected(o)" :icon="Check" :size="18" class="check" />
            </button>
            <p v-if="!shown.length && noMatches" class="type-body-medium muted empty">{{ noMatches }}</p>
          </div>
        </div>
      </Transition>
    </Teleport>
  </div>
</template>

<style scoped>
.composer-menu { display: inline-flex; min-width: 0; }
.menu {
  position: fixed; display: flex; flex-direction: column; gap: var(--app-space-1);
  min-width: 240px; width: max-content; max-width: min(var(--app-width-menu), calc(100vw - 16px)); padding: var(--app-space-2);
  background: var(--md-sys-color-surface-container-high); color: var(--md-sys-color-on-surface);
  border: 1px solid var(--md-sys-color-outline-variant); border-radius: var(--md-sys-shape-corner-large); box-shadow: var(--app-elevation-2);
}
.head { display: flex; flex-direction: column; gap: 2px; padding: var(--app-space-1) var(--app-space-2) var(--app-space-1); }
.search {
  display: flex; align-items: center; gap: var(--app-space-2); flex: none; height: 36px; margin-bottom: var(--app-space-1); padding: 0 var(--app-space-2);
  border-radius: var(--md-sys-shape-corner-small); background: var(--md-sys-color-surface-container-highest);
}
.search input { flex: 1; min-width: 0; border: 0; outline: none; background: transparent; color: inherit; font: inherit; }
.search:focus-within { box-shadow: inset 0 0 0 1px var(--md-sys-color-primary); }
.list { display: flex; flex-direction: column; gap: 2px; flex: 0 1 auto; min-height: 0; max-height: 320px; }
.item {
  display: flex; flex: none; align-items: center; gap: var(--app-space-2); min-height: 36px; padding: 6px var(--app-space-2);
  border: 0; border-radius: var(--md-sys-shape-corner-small); background: transparent; color: inherit; font: inherit; text-align: start; cursor: pointer;
}
/* The highlight the arrows move, shown like a hover: the same state layer. */
.item.active::before { opacity: var(--md-sys-state-hover-state-layer-opacity); }
.item[aria-selected='true'] { color: var(--md-sys-color-primary); }
.text { display: flex; flex-direction: column; flex: 1; min-width: 0; }
/* Model ids are long single words: wrap them in the list rather than cut them. */
.name, .meta { overflow-wrap: anywhere; }
.item[aria-selected='true'] .meta { color: var(--md-sys-color-on-surface-variant); }
.check { flex: none; }
.item[aria-disabled='true'] { opacity: 0.38; cursor: default; }
.empty { margin: 0; padding: var(--app-space-2); }
/* With a search, one size whatever it leaves: the menu as wide as a menu grows (within the window),
   the list as tall as it may be (within the room the placement gives it), "no matches" in its middle. */
.menu.fixed { width: min(var(--app-width-menu), calc(100vw - 16px)); }
.menu.fixed .list { flex: 1 1 320px; height: 320px; max-height: none; }
.menu.fixed .empty { margin: auto; text-align: center; }
</style>
