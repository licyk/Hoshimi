<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue';
import { conversationsApi } from '@/api/conversations';
import type { TerminalView } from '@/api/types';
import { elapsed } from '@/format';
import { useI18n } from '@/i18n';
import { AppButton, AppIcon, IconButton, TRANSITIONS, TweenNumber, icons, keepLeavingInPlace, staggerStyle } from '@/ui';

/** Mounted for one conversation while /ps or /stop is open. Reads never consume tool output. */
const props = defineProps<{ conversationId: string | null; mode: 'terminals' | 'stop'; query: string }>();
const emit = defineEmits<{ stop: []; changed: [] }>();
const { t } = useI18n();
const terminals = ref<TerminalView[]>([]);
const loaded = ref(false);
const refreshing = ref(false);
const stopping = ref(false);
const stopped = ref(false);
const error = ref('');
const now = ref(Date.now());
const list = ref<HTMLElement | null>(null);
const running = computed(() => terminals.value.filter((term) => term.running));
const visible = computed(() => {
  if (props.mode === 'stop') return running.value;
  const query = props.query.trim().toLowerCase();
  return terminals.value.filter((term) => `${term.command}\n${term.cwd}\n${term.id}`.toLowerCase().includes(query));
});
const canStop = computed(() => loaded.value && !stopping.value && !error.value && running.value.length > 0);
let disposed = false;
let revision = 0;
let timer: ReturnType<typeof setTimeout> | undefined;

async function refresh() {
  clearTimeout(timer);
  const ticket = ++revision;
  refreshing.value = true;
  try {
    const data = props.conversationId ? await conversationsApi.terminals(props.conversationId) : { terminals: [] };
    if (disposed || ticket !== revision) return;
    terminals.value = data.terminals;
    loaded.value = true;
    error.value = '';
    now.value = Date.now();
  } catch (cause) {
    if (!disposed && ticket === revision) error.value = cause instanceof Error ? cause.message : t('common.error');
  } finally {
    if (!disposed && ticket === revision) {
      refreshing.value = false;
      // Poll only while this pane is visible; one request at a time, including after a failure.
      if (props.conversationId) timer = setTimeout(() => void refresh(), 2000);
    }
  }
}

async function stopAll() {
  if (!canStop.value || !props.conversationId) return;
  clearTimeout(timer);
  ++revision; // A read started before this stop must never restore the old list.
  stopping.value = true;
  refreshing.value = false;
  stopped.value = false;
  try {
    await conversationsApi.stopTerminals(props.conversationId);
    if (disposed) return;
    terminals.value = [];
    stopped.value = true;
    emit('changed');
    await refresh();
  } catch (cause) {
    if (!disposed) error.value = cause instanceof Error ? cause.message : t('common.error');
  } finally {
    stopping.value = false;
  }
}

function onKey(event: KeyboardEvent): boolean {
  if (event.key === 'Enter' && !event.shiftKey) {
    event.preventDefault();
    if (props.mode === 'stop') void stopAll();
    else if (canStop.value) emit('stop');
    return true;
  }
  const step = event.key === 'ArrowDown' || (event.ctrlKey && event.key === 'n') ? 1 : event.key === 'ArrowUp' || (event.ctrlKey && event.key === 'p') ? -1 : 0;
  if (!step) return false;
  event.preventDefault();
  if (list.value) list.value.scrollTop += step * 64;
  return true;
}
defineExpose({ onKey });
onMounted(() => void refresh());
onBeforeUnmount(() => { disposed = true; clearTimeout(timer); });
</script>

<template>
  <div class="background-panel">
    <div class="summary">
      <AppIcon :icon="mode === 'stop' ? icons.CircleStop : icons.Terminal" :size="20" class="muted" />
      <span class="type-body-medium">{{ t('playground.background.scope') }}</span>
      <span v-if="loaded" class="count type-label-medium"><TweenNumber :value="running.length" /> {{ t('playground.background.running') }}</span>
      <IconButton :icon="icons.RefreshCw" :label="t('common.refresh')" :disabled="refreshing || stopping" @pointerdown.prevent @click="refresh" />
    </div>
    <p v-if="mode === 'stop' && running.length" class="note type-body-small muted">{{ t('playground.background.stopHelp') }}</p>
    <p v-if="error" role="alert" class="error type-body-small">{{ error }}</p>
    <p v-if="!loaded && !error" role="status" class="empty type-body-medium muted">{{ t('playground.background.loading') }}</p>
    <Transition :name="TRANSITIONS.collapse">
      <p v-if="stopped" role="status" class="success type-body-medium"><AppIcon :icon="icons.CircleCheck" :size="18" />{{ t('playground.background.stopped') }}</p>
    </Transition>
    <div ref="list" class="sessions app-scroll app-scroll-auto" :aria-busy="refreshing || stopping">
      <TransitionGroup tag="ul" :name="TRANSITIONS.list" v-bind="keepLeavingInPlace" appear :aria-label="t('playground.terminals')">
        <li v-for="(term, i) in visible" :key="term.id" class="session" :style="staggerStyle(i)">
          <AppIcon :icon="term.running ? icons.Terminal : icons.CircleCheck" :size="18" class="muted session-icon" />
          <div class="details">
            <code class="command type-body-medium">{{ term.command }}</code>
            <span class="cwd type-body-small muted">{{ term.cwd }}</span>
            <span class="meta type-body-small muted">
              <span>#{{ term.id }}</span>
              <span :class="{ live: term.running }">{{ term.running ? t('playground.background.running') : t('playground.background.exited', { code: term.exit_code ?? '—' }) }}</span>
              <span v-if="term.running">{{ elapsed(Math.max(0, now - term.started_at * 1000)) }}</span>
            </span>
          </div>
        </li>
      </TransitionGroup>
      <p v-if="loaded && !visible.length && !error && !stopped" class="empty type-body-medium muted">
        {{ t(mode === 'terminals' && query.trim() && terminals.length ? 'playground.background.noMatches' : 'playground.background.empty') }}
      </p>
    </div>
    <div class="actions" @pointerdown.prevent>
      <AppButton v-if="mode === 'terminals'" variant="text" :icon="icons.CircleStop" :disabled="!canStop" @click="emit('stop')">{{ t('playground.background.stopAll') }}</AppButton>
      <AppButton v-else :icon="icons.CircleStop" :disabled="!canStop" :loading="stopping" @click="stopAll">{{ t(stopping ? 'playground.background.stopping' : 'playground.background.stopAll') }}</AppButton>
    </div>
  </div>
</template>

<style scoped>
.background-panel { display: flex; flex-direction: column; gap: var(--app-space-2); min-height: 0; }
.summary { display: flex; align-items: center; gap: var(--app-space-2); flex: none; flex-wrap: wrap; }
.count { margin-inline-start: auto; color: var(--md-sys-color-primary); font-variant-numeric: tabular-nums; }
.sessions { min-height: 0; }
.sessions ul { position: relative; display: flex; flex-direction: column; gap: var(--app-space-2); margin: 0; padding: 0; list-style: none; }
.session { display: flex; gap: var(--app-space-3); padding: var(--app-space-3); border-radius: var(--md-sys-shape-corner-medium); background: var(--md-sys-color-surface-container-low); }
.session-icon { flex: none; margin-top: 2px; }
.details { display: flex; flex-direction: column; gap: var(--app-space-1); min-width: 0; }
.command, .cwd { white-space: pre-wrap; overflow-wrap: anywhere; }
.command { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }
.meta { display: flex; flex-wrap: wrap; gap: var(--app-space-3); font-variant-numeric: tabular-nums; }
.live, .success { color: var(--md-sys-color-primary); }
.success { display: flex; align-items: center; gap: var(--app-space-2); }
.note, .error, .success { margin: 0; overflow-wrap: anywhere; flex: none; }
.error { color: var(--md-sys-color-error); }
.empty { padding: var(--app-space-4) var(--app-space-2); margin: 0; text-align: center; }
.actions { display: flex; justify-content: flex-end; flex: none; }
</style>
