<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue';
import type { ContextView } from '@/api/types';
import { filterOptions, find, match, parseDraft, unavailable, type SlashCommand, type SlashContext, type SlashId, type SlashPage } from '@/components/chat/slash/commands';
import StatusPanel from '@/components/chat/StatusPanel.vue';
import { useI18n } from '@/i18n';
import { AppIcon, KeyHint, TRANSITIONS, icons, keepLeavingInPlace, prefersReducedMotion, staggerStyle, useAxisDirection, type ComposerOption } from '@/ui';
import { useLayer } from '@/ui/layers';
import { milliseconds } from '@/ui/motion/transitions';

/**
 * The menu typing ``/`` in the composer opens, over the composer and as wide as it. The composer's
 * text is its only input: ``/mo`` narrows the commands (the part matched in bold), and a command
 * that chooses from a list opens it in place once a space follows its name, the words after it
 * narrowing that list (``/model gpt``). The focus stays in the text box throughout; it gives the
 * menu its keys first (``onKey``): the arrows move a highlight that slides from row to row, Tab
 * completes a command's name, Enter runs it (or opens its list, or picks from the list), Escape
 * closes the menu until the command is changed. A command that cannot run now (a reply is under
 * way, there is nothing to compact yet) says why, and shakes if chosen all the same.
 *
 * It opens growing out of the composer, its rows coming in one after another; rows that stop
 * matching fade as the rest close up, and a list opened from a command slides in from the side
 * (and back out to it), the menu easing to its new height.
 */
const props = defineProps<{
  draft: string;
  /** What the menu stands over: the composer's surface. */
  anchor: () => HTMLElement | null;
  context: SlashContext;
  models: ComposerOption[];
  efforts: ComposerOption[];
  tools: ComposerOption[];
  permissions: ComposerOption[];
  current: { model: string | null; effort: string; tools: string[]; permission: string };
  status: ContextView | null;
  statusLabels: { effort: string; tools: string[]; approval: string };
}>();
const emit = defineEmits<{ 'update:draft': [text: string]; run: [id: SlashId, argument: string]; choose: [page: SlashPage, value: string]; page: [page: SlashPage] }>();
const { t } = useI18n();
const uid = `slash-${Math.random().toString(36).slice(2, 8)}`;

const label = (c: SlashCommand) => t(`playground.slash.commands.${c.id}.label`);
const help = (c: SlashCommand) => t(`playground.slash.commands.${c.id}.help`);

const parsed = computed(() => parseDraft(props.draft));
/** The command named before a space: its list (or the status) is open, or it waits for Enter with its words. */
const named = computed(() => (parsed.value?.spaced ? find(parsed.value.name) : undefined));
type View = 'commands' | SlashPage;
const view = computed<View | null>(() => {
  const draft = parsed.value;
  if (!draft) return null;
  if (!draft.spaced) return 'commands';
  return named.value ? (named.value.page ?? 'commands') : null;
});
const blocked = computed(() => (named.value ? unavailable(named.value.id, props.context) : null));
const matches = computed(() => {
  const draft = parsed.value;
  if (!draft) return [];
  if (draft.spaced) return named.value ? [{ command: named.value, range: null }] : [];
  return match(draft.name, (c) => [label(c)]);
});
const query = computed(() => parsed.value?.rest ?? '');
const options = computed<ComposerOption[]>(() => {
  if (blocked.value) return [];
  const list = { models: props.models, efforts: props.efforts, tools: props.tools, permissions: props.permissions }[view.value as 'models'] ?? [];
  return filterOptions(list, query.value);
});
const count = computed(() => (view.value === 'commands' ? matches.value.length : view.value === 'status' ? 0 : options.value.length));
const selectedValue = (o: ComposerOption) =>
  view.value === 'models' ? o.value === props.current.model
  : view.value === 'efforts' ? o.value === props.current.effort
  : view.value === 'permissions' ? o.value === props.current.permission
  : props.current.tools.includes(o.value);

// Closed by Escape (or a press elsewhere) until what the draft names changes.
const token = computed(() => (parsed.value ? `${view.value}|${parsed.value.name.toLowerCase()}|${parsed.value.spaced}` : ''));
const dismissed = ref('');
const open = computed(() => !!view.value && token.value !== dismissed.value && (view.value !== 'commands' || matches.value.length > 0));
const dismiss = () => (dismissed.value = token.value);

const active = ref(0);
watch([token, query], () => {
  // A list opens on its current choice; anything typed starts the highlight over at the first match.
  const at = view.value !== 'commands' && !query.value ? options.value.findIndex(selectedValue) : -1;
  active.value = Math.max(0, at);
});
watch(view, (next) => {
  if (next && next !== 'commands') emit('page', next);
});

const menu = ref<HTMLElement | null>(null);
const body = ref<HTMLElement | null>(null);
const list = ref<HTMLElement | null>(null);
const layer = useLayer({ kind: 'menu', open: () => open.value, close: dismiss, elements: () => [props.anchor(), menu.value] });

// -- placing ----------------------------------------------------------------------------- //
const GAP = 8;
const MARGIN = 8;
const position = ref<Record<string, string>>({});
function place() {
  const anchor = props.anchor()?.getBoundingClientRect();
  if (!anchor) return;
  const width = Math.min(Math.max(anchor.width, 280), window.innerWidth - 2 * MARGIN);
  const left = Math.min(Math.max(MARGIN, anchor.left), window.innerWidth - width - MARGIN);
  position.value = {
    left: `${left}px`,
    width: `${width}px`,
    bottom: `${window.innerHeight - anchor.top + GAP}px`,
    maxHeight: `${Math.max(180, Math.min(440, anchor.top - GAP - MARGIN))}px`,
    '--popover-origin': `bottom ${anchor.left - left + anchor.width / 2}px`,
    '--popover-shift': '8px',
  };
}
const onReflow = (e?: Event) => {
  if (open.value && !(e?.type === 'scroll' && menu.value?.contains(e.target as Node))) place();
};
function listen(on: boolean) {
  const method = on ? 'addEventListener' : 'removeEventListener';
  window[method]('scroll', onReflow, true);
  window[method]('resize', onReflow);
}
watch(open, (isOpen) => {
  listen(isOpen);
  if (isOpen) place();
});
// The composer grows with what is typed: the menu stays on top of it.
watch(() => props.draft, () => open.value && void nextTick(place));
onBeforeUnmount(() => listen(false));

// -- the highlight -------------------------------------------------------------------------- //
const indicator = ref<{ top: number; height: number } | null>(null);
const rowAt = (i: number) => list.value?.querySelectorAll<HTMLElement>('[data-row]:not(.list-leave-active)')[i] ?? null;
async function follow() {
  await nextTick();
  const row = rowAt(active.value);
  indicator.value = row ? { top: row.offsetTop, height: row.offsetHeight } : null;
  row?.scrollIntoView({ block: 'nearest' });
}
watch([active, count, view, open], () => void follow());

// A list opening from a command slides in from the side; the menu eases to its new height.
const direction = useAxisDirection(computed(() => view.value ?? 'commands'), () => ['commands', 'models', 'efforts', 'tools', 'permissions', 'status']);
watch(
  view,
  (next, previous) => {
    const el = body.value;
    if (!el || !next || !previous || next === previous || prefersReducedMotion() || typeof el.animate !== 'function') return;
    const from = el.getBoundingClientRect().height;
    void nextTick(() => {
      const to = el.getBoundingClientRect().height;
      if (Math.abs(to - from) < 1) return;
      const root = getComputedStyle(document.documentElement);
      const duration = milliseconds(root.getPropertyValue('--md-sys-motion-duration-medium2')) || 300;
      const easing = root.getPropertyValue('--md-sys-motion-easing-emphasized').trim() || 'cubic-bezier(0.2, 0, 0, 1)';
      el.animate([{ height: `${from}px`, overflow: 'hidden' }, { height: `${to}px`, overflow: 'hidden' }], { duration, easing });
    });
  },
  { flush: 'pre' },
);

// -- acting ---------------------------------------------------------------------------------- //
const shaking = ref(-1);
function refuse(i: number) {
  shaking.value = -1;
  void nextTick(() => {
    shaking.value = i;
    setTimeout(() => shaking.value === i && (shaking.value = -1), 420);
  });
}
function complete(c: SlashCommand) {
  emit('update:draft', `/${c.id} `);
}
function activate(i: number) {
  if (view.value === 'commands') {
    const c = matches.value[i]?.command;
    if (!c) return;
    if (unavailable(c.id, props.context)) return refuse(i);
    if (c.page && !parsed.value?.spaced) return complete(c);
    emit('run', c.id, parsed.value?.spaced ? query.value : '');
    return;
  }
  if (view.value === 'status') return emit('update:draft', '');
  const o = options.value[i];
  if (!o) return;
  if (o.disabled) return refuse(i);
  emit('choose', view.value as SlashPage, o.value);
}
/** Back from a list to the commands. */
const back = () => emit('update:draft', '/');

/** The composer's keys, before it acts on them; true when the menu dealt with one. */
function onKey(e: KeyboardEvent): boolean {
  if (!open.value || e.isComposing || e.keyCode === 229) return false;
  const step = e.key === 'ArrowDown' || (e.ctrlKey && e.key === 'n') ? 1 : e.key === 'ArrowUp' || (e.ctrlKey && e.key === 'p') ? -1 : 0;
  if (step) {
    e.preventDefault();
    if (count.value) active.value = (active.value + step + count.value) % count.value;
    return true;
  }
  if (e.key === 'Escape') {
    e.preventDefault();
    dismiss();
    return true;
  }
  if (e.key === 'Tab' && !e.shiftKey) {
    e.preventDefault();
    const c = view.value === 'commands' ? matches.value[active.value]?.command : undefined;
    if (c && !parsed.value?.spaced) complete(c);
    return true;
  }
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault();
    activate(active.value);
    return true;
  }
  return false;
}
/** Open again what Escape closed (the draft names a command that lists): Enter on a closed menu. */
const reopen = () => (dismissed.value = '');

const title = computed(() => {
  switch (view.value) {
    case 'models': return t('playground.model');
    case 'efforts': return t('playground.effort');
    case 'tools': return t('playground.tools');
    case 'permissions': return t('settings.approval');
    case 'status': return t('playground.slash.commands.status.label');
    default: return t('playground.slash.title');
  }
});
const nameParts = (c: SlashCommand, range: [number, number] | null) => {
  const text = `/${c.id}`;
  return range ? [text.slice(0, range[0]), text.slice(range[0], range[1]), text.slice(range[1])] : [text, '', ''];
};
const describe = (c: SlashCommand) => {
  const reason = unavailable(c.id, props.context);
  if (reason) return t(reason);
  if (parsed.value?.spaced && c.argument && query.value) return t('playground.slash.runWith', { text: query.value });
  return help(c);
};
const combobox = computed(() => ({ expanded: open.value, controls: `${uid}-list`, active: count.value ? `${uid}-${active.value}` : undefined }));

defineExpose({ onKey, reopen, combobox, open });
</script>

<template>
  <Teleport to="body">
    <Transition :name="TRANSITIONS.popover">
      <div v-if="open" ref="menu" class="slash-menu" role="dialog" :aria-label="title" :style="{ ...position, zIndex: layer.zIndex.value }">
        <header class="head">
          <button v-if="view !== 'commands'" type="button" class="back state-layer" :aria-label="t('playground.slash.back')" :title="t('playground.slash.back')" @pointerdown.prevent @click="back">
            <AppIcon :icon="icons.ArrowLeft" :size="18" />
          </button>
          <span class="type-title-small">{{ title }}</span>
          <span v-if="view !== 'commands' && query" class="type-body-small muted query">“{{ query }}”</span>
          <span class="keys type-body-small muted" aria-hidden="true">
            <KeyHint keys="ArrowUp" /><KeyHint keys="ArrowDown" />
            <KeyHint keys="Enter" />
            <template v-if="view === 'commands'"><KeyHint keys="Tab" /></template>
            <KeyHint keys="Escape" />
          </span>
        </header>
        <div ref="body" class="body" :style="{ '--axis-dir': direction }">
          <Transition :name="TRANSITIONS.sharedAxisX">
            <div v-if="view === 'status'" key="status" class="pane status app-scroll app-scroll-auto">
              <StatusPanel v-if="status" :view="status" :effort-label="statusLabels.effort" :tools="statusLabels.tools" :approval-label="statusLabels.approval" />
              <p v-else class="type-body-medium muted empty">{{ t('playground.slash.statusLoading') }}</p>
            </div>
            <div v-else :key="view ?? 'commands'" class="pane">
              <div :id="`${uid}-list`" ref="list" class="list app-scroll app-scroll-auto" role="listbox" :aria-label="title" :aria-multiselectable="view === 'tools' || undefined">
                <span v-if="indicator" class="indicator" aria-hidden="true" :style="{ transform: `translateY(${indicator.top}px)`, height: `${indicator.height}px` }" />
                <TransitionGroup :name="TRANSITIONS.list" v-bind="keepLeavingInPlace" appear>
                  <template v-if="view === 'commands'">
                    <button
                      v-for="(m, i) in matches"
                      :id="`${uid}-${i}`"
                      :key="m.command.id"
                      type="button"
                      role="option"
                      data-row
                      tabindex="-1"
                      class="row"
                      :class="{ active: i === active, shake: i === shaking, unavailable: !!unavailable(m.command.id, context) }"
                      :style="staggerStyle(i)"
                      :aria-selected="i === active"
                      :aria-disabled="!!unavailable(m.command.id, context) || undefined"
                      @pointerdown.prevent
                      @pointermove="active = i"
                      @click="activate(i)"
                    >
                      <AppIcon :icon="m.command.icon" :size="18" class="icon" />
                      <code class="name type-body-medium"><span>{{ nameParts(m.command, m.range)[0] }}</span><b>{{ nameParts(m.command, m.range)[1] }}</b><span>{{ nameParts(m.command, m.range)[2] }}</span></code>
                      <span class="text">
                        <span class="type-body-medium">{{ label(m.command) }}</span>
                        <span class="type-body-small muted desc">{{ describe(m.command) }}</span>
                      </span>
                      <AppIcon v-if="m.command.page && !unavailable(m.command.id, context)" :icon="icons.ChevronRight" :size="18" class="muted more" />
                    </button>
                  </template>
                  <template v-else>
                    <button
                      v-for="(o, i) in options"
                      :id="`${uid}-${i}`"
                      :key="o.value"
                      type="button"
                      role="option"
                      data-row
                      tabindex="-1"
                      class="row option"
                      :class="{ active: i === active, shake: i === shaking, unavailable: o.disabled }"
                      :style="staggerStyle(i)"
                      :aria-selected="selectedValue(o)"
                      :aria-disabled="o.disabled || undefined"
                      @pointerdown.prevent
                      @pointermove="active = i"
                      @click="activate(i)"
                    >
                      <span class="text">
                        <span class="type-body-medium name-text">{{ o.label }}</span>
                        <span v-if="o.description" class="type-body-small muted desc">{{ o.description }}</span>
                      </span>
                      <span v-if="view === 'tools'" class="switch" :class="{ on: selectedValue(o) }" aria-hidden="true"><span class="handle" /></span>
                      <AppIcon v-else-if="selectedValue(o)" :icon="icons.Check" :size="18" class="check" />
                    </button>
                  </template>
                </TransitionGroup>
                <p v-if="view !== 'commands' && !options.length" class="type-body-medium muted empty">{{ blocked ? t(blocked) : t('models.none') }}</p>
              </div>
            </div>
          </Transition>
        </div>
      </div>
    </Transition>
  </Teleport>
</template>

<style scoped>
.slash-menu {
  position: fixed; display: flex; flex-direction: column; min-width: 0; padding: var(--app-space-2);
  background: var(--md-sys-color-surface-container-high); color: var(--md-sys-color-on-surface);
  border: 1px solid var(--md-sys-color-outline-variant); border-radius: var(--md-sys-shape-corner-large); box-shadow: var(--app-elevation-3, var(--app-elevation-2));
}
.head { display: flex; align-items: center; gap: var(--app-space-2); flex: none; min-width: 0; padding: var(--app-space-1) var(--app-space-2) var(--app-space-2); }
.back {
  display: grid; place-items: center; flex: none; width: 28px; height: 28px; margin-inline-start: -4px; padding: 0; border: 0;
  border-radius: var(--md-sys-shape-corner-full); background: transparent; color: var(--md-sys-color-on-surface-variant); cursor: pointer;
}
.query { min-width: 0; overflow: hidden; white-space: nowrap; text-overflow: ellipsis; }
.keys { display: inline-flex; align-items: center; gap: 4px; margin-inline-start: auto; flex: none; }
/* The panes slide past one another inside it (shared-axis-x takes the one leaving out of the flow). */
.body { position: relative; display: flex; flex-direction: column; flex: 1 1 auto; min-height: 0; }
.pane { display: flex; flex-direction: column; min-height: 0; flex: 1 1 auto; }
.pane.status { padding: var(--app-space-1) var(--app-space-2) var(--app-space-2); }
.list { position: relative; display: flex; flex-direction: column; gap: 2px; flex: 1 1 auto; min-height: 0; }
/* The highlight: one shape that slides to the row the arrows reach, rather than each row flashing. */
.indicator {
  position: absolute; top: 0; inset-inline: 0; z-index: 0; border-radius: var(--md-sys-shape-corner-small); pointer-events: none;
  background: color-mix(in srgb, var(--md-sys-color-on-surface) 8%, transparent);
  transition: transform var(--md-sys-motion-duration-short4) var(--md-sys-motion-easing-emphasized), height var(--md-sys-motion-duration-short4) var(--md-sys-motion-easing-emphasized);
}
.row {
  position: relative; z-index: 1; display: flex; align-items: center; gap: var(--app-space-3); flex: none; min-height: 44px; padding: 6px var(--app-space-2);
  border: 0; border-radius: var(--md-sys-shape-corner-small); background: transparent; color: inherit; font: inherit; text-align: start; cursor: pointer;
}
.icon { flex: none; color: var(--md-sys-color-on-surface-variant); transition: color var(--md-sys-motion-duration-short4) var(--md-sys-motion-easing-standard); }
.row.active .icon { color: var(--md-sys-color-primary); }
/* The names in one column, as wide as the longest (/permissions). */
.name { flex: none; width: 13ch; font-family: ui-monospace, SFMono-Regular, Menlo, monospace; color: var(--md-sys-color-on-surface-variant); }
.name b { color: var(--md-sys-color-primary); font-weight: 700; }
.text { display: flex; flex-direction: column; flex: 1; min-width: 0; }
.desc, .name-text { overflow-wrap: anywhere; }
.row.unavailable { cursor: default; }
.row.unavailable .text, .row.unavailable .name, .row.unavailable .icon { opacity: 0.5; }
.row[aria-selected='true'].option .name-text { color: var(--md-sys-color-primary); }
.more, .check { flex: none; }
.check { color: var(--md-sys-color-primary); }
/* Chosen when it cannot be: it shakes its head. */
.row.shake { animation: slash-shake 0.4s var(--md-sys-motion-easing-standard); }
@keyframes slash-shake { 20%, 60% { transform: translateX(-4px); } 40%, 80% { transform: translateX(4px); } }
.switch {
  position: relative; flex: none; width: 40px; height: 24px; border-radius: 12px;
  border: 2px solid var(--md-sys-color-outline); background: var(--md-sys-color-surface-container-highest);
  transition: background var(--md-sys-motion-duration-short4) var(--md-sys-motion-easing-standard), border-color var(--md-sys-motion-duration-short4) var(--md-sys-motion-easing-standard);
}
.handle {
  position: absolute; top: 50%; left: 4px; width: 12px; height: 12px; border-radius: 50%; background: var(--md-sys-color-outline); transform: translateY(-50%);
  transition: all var(--md-sys-motion-duration-short4) var(--md-sys-motion-easing-emphasized);
}
.switch.on { border-color: var(--md-sys-color-primary); background: var(--md-sys-color-primary); }
.switch.on .handle { left: 18px; width: 16px; height: 16px; background: var(--md-sys-color-on-primary); }
.empty { margin: 0; padding: var(--app-space-4) var(--app-space-2); text-align: center; }
@media (max-width: 599px) {
  .keys { display: none; }
  .name { width: auto; min-width: 0; }
  .row:not(.option) .desc { display: none; }
}
@media (prefers-reduced-motion: reduce) {
  .indicator, .icon, .switch, .handle { transition: none; }
  .row.shake { animation: none; }
}
</style>
