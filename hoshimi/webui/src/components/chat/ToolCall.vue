<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch, type Component } from 'vue';
import type { ApprovalDecision, ToolStep } from '@/api/types';
import { duration } from '@/format';
import { useI18n } from '@/i18n';
import { AppButton, AppIcon, Collapse, icons } from '@/ui';

/**
 * One tool call in a reply, as a line that opens: what it did in a few words ("Ran `npm test`",
 * "Edited app.py"), how it is going (preparing, waiting for approval, running with the time so far,
 * done, failed, declined, stopped) and how long it took. Opened, it shows what was asked (the command,
 * the patch with its added and removed lines, or the arguments) and what came back. A call that waits
 * for approval stands open with the question and the answers under it.
 */
const props = defineProps<{ step: ToolStep }>();
const emit = defineEmits<{ approve: [ApprovalDecision] }>();
const { t } = useI18n();
const id = `tool-${Math.random().toString(36).slice(2, 10)}`;
const open = ref(false);
const answering = ref(false);
/** Opened to ask: folded again once answered and done, as the other calls are. */
let openedToAsk = false;

const args = computed<Record<string, unknown>>(() => {
  try {
    const value = JSON.parse(props.step.arguments || '{}');
    return value && typeof value === 'object' && !Array.isArray(value) ? value : {};
  } catch {
    return {};
  }
});
const str = (key: string) => (typeof args.value[key] === 'string' ? (args.value[key] as string) : '');
const firstLine = (text: string) => {
  const line = text.trim().split('\n')[0] ?? '';
  return text.trim().includes('\n') ? `${line} …` : line;
};
/** The files a patch names, in order. */
const patchFiles = computed(() => [...str('input').matchAll(/^\*\*\* (?:Add|Update|Delete) File: (.+)$/gm)].map((m) => m[1].trim()));
const host = (url: string) => {
  try {
    const u = new URL(url.includes('://') ? url : `https://${url}`);
    return u.host + (u.pathname === '/' ? '' : u.pathname);
  } catch {
    return url;
  }
};

const KINDS: Record<string, { icon: Component; verb: string }> = {
  read: { icon: icons.FileText, verb: 'read' },
  apply_patch: { icon: icons.FilePen, verb: 'edit' },
  shell: { icon: icons.Terminal, verb: 'run' },
  exec_command: { icon: icons.SquareTerminal, verb: 'start' },
  write_stdin: { icon: icons.SquareTerminal, verb: 'check' },
  kill_session: { icon: icons.SquareTerminal, verb: 'stopSession' },
  web_search: { icon: icons.Search, verb: 'search' },
  web_fetch: { icon: icons.Globe, verb: 'open' },
};
const kind = computed(() => KINDS[props.step.name] ?? { icon: icons.Wrench, verb: '' });
/** The line's words: a verb, and what it acted on (in code type for paths and commands). */
const summary = computed(() => {
  const s = props.step;
  const verb = kind.value.verb === 'check' && str('chars') ? 'typeInto' : kind.value.verb;
  const label = verb ? t(`playground.toolVerbs.${verb}`) : s.name;
  switch (s.name) {
    case 'read':
      return { label, detail: str('path'), code: true };
    case 'apply_patch':
      return { label, detail: patchFiles.value.join(', '), code: true };
    case 'shell':
      return { label, detail: firstLine(str('command')), code: true };
    case 'exec_command':
      return { label, detail: firstLine(str('cmd')), code: true };
    case 'write_stdin':
    case 'kill_session':
      return { label, detail: args.value.session_id !== undefined ? t('playground.toolSession', { id: String(args.value.session_id) }) : '', code: false };
    case 'web_search':
      return { label, detail: str('query') ? `“${str('query')}”` : '', code: false };
    case 'web_fetch':
      return { label, detail: host(str('url')), code: false };
    default:
      return { label, detail: '', code: false };
  }
});

// The time so far while it runs; the kept time once it is done.
const started = ref(Date.now());
const now = ref(Date.now());
let timer: ReturnType<typeof setInterval> | undefined;
watch(
  () => props.step.status,
  (status, before) => {
    clearInterval(timer);
    timer = undefined;
    if (status === 'running') {
      if (before !== 'running') started.value = now.value = Date.now();
      timer = setInterval(() => (now.value = Date.now()), 1000);
    }
    if (status === 'waiting' && !open.value) open.value = openedToAsk = true;
    if (before === 'waiting' && status !== 'waiting') answering.value = false;
    if (openedToAsk && status !== 'waiting' && status !== 'running') open.value = openedToAsk = false;
  },
  { immediate: true },
);
onBeforeUnmount(() => clearInterval(timer));

const state = computed(() => {
  const s = props.step;
  switch (s.status) {
    case 'pending':
      return { icon: icons.Loader2, text: t('playground.toolPreparing'), tone: 'busy' };
    case 'waiting':
      return { icon: icons.ShieldAlert, text: t('playground.toolWaiting'), tone: 'ask' };
    case 'running':
      return { icon: icons.Loader2, text: now.value - started.value >= 1000 ? duration(Math.floor((now.value - started.value) / 1000) * 1000) : '', tone: 'busy' };
    case 'done':
      return { icon: icons.Check, text: s.ms >= 100 ? duration(s.ms) : '', tone: 'done' };
    case 'denied':
      return { icon: icons.Ban, text: t('playground.toolDenied'), tone: 'off' };
    case 'stopped':
      return { icon: icons.CircleStop, text: t('playground.toolStopped'), tone: 'off' };
    default:
      return { icon: icons.X, text: s.exit_code !== null && s.exit_code !== undefined ? t('playground.toolExit', { code: String(s.exit_code) }) : t('playground.toolFailed'), tone: 'failed' };
  }
});

/** What was asked: the command, the patch, or the arguments as they were sent. */
const command = computed(() => str('command') || str('cmd'));
const patchLines = computed(() =>
  str('input')
    .replace(/\n$/, '')
    .split('\n')
    .map((line) => ({ line, kind: line.startsWith('***') || line.startsWith('@@') ? 'head' : line.startsWith('+') ? 'add' : line.startsWith('-') ? 'remove' : '' })),
);
const argumentsText = computed(() => {
  if (!props.step.arguments) return '';
  try {
    return JSON.stringify(JSON.parse(props.step.arguments), null, 2);
  } catch {
    return props.step.arguments;
  }
});
/** What the call needs leave for, as the server says (``command``, ``edit``, ``edit-outside``, ``read-outside``). */
const QUESTIONS: Record<string, string> = {
  command: 'playground.approveRun',
  edit: 'playground.approveEdit',
  'edit-outside': 'playground.approveEditOutside',
  'read-outside': 'playground.approveRead',
};
const question = computed(() => t(QUESTIONS[props.step.approval] ?? 'playground.approveCall'));

function answer(decision: ApprovalDecision) {
  answering.value = true;
  emit('approve', decision);
}
</script>

<template>
  <div class="tool-call" :class="[`is-${step.status}`, { open }]">
    <button type="button" class="line state-layer type-body-medium" :aria-expanded="open" :aria-controls="id" @click="(open = !open), (openedToAsk = false)">
      <AppIcon :icon="kind.icon" :size="18" class="kind" />
      <span class="what">
        <span class="verb">{{ summary.label }}</span>
        <code v-if="summary.detail && summary.code" class="detail">{{ summary.detail }}</code>
        <span v-else-if="summary.detail" class="detail">{{ summary.detail }}</span>
      </span>
      <span class="state" :class="state.tone">
        <AppIcon :icon="state.icon" :size="18" :class="{ spin: state.tone === 'busy' }" />
        <span v-if="state.text" class="type-body-small num">{{ state.text }}</span>
      </span>
      <AppIcon :icon="icons.ChevronDown" :size="18" class="chevron" />
    </button>
    <Collapse :show="open">
      <div :id="id" class="body">
        <pre v-if="command" class="block command type-body-small"><span class="prompt" aria-hidden="true">$ </span>{{ command }}</pre>
        <pre v-else-if="step.name === 'apply_patch' && str('input')" class="block patch type-body-small"><span v-for="(l, i) in patchLines" :key="i" :class="l.kind">{{ l.line }}
</span></pre>
        <pre v-else-if="argumentsText && step.status !== 'pending'" class="block type-body-small">{{ argumentsText }}</pre>
        <pre v-if="step.output" class="block output app-scroll type-body-small" :class="{ failed: step.status === 'error' }">{{ step.output }}</pre>
      </div>
    </Collapse>
    <div v-if="step.status === 'waiting'" class="approval" role="group" :aria-label="question">
      <span class="type-body-medium question"><AppIcon :icon="icons.ShieldAlert" :size="18" />{{ question }}</span>
      <div class="answers">
        <AppButton variant="text" :disabled="answering" @click="answer('deny')">{{ t('playground.deny') }}</AppButton>
        <AppButton variant="tonal" :disabled="answering" :title="t('playground.allowAlwaysHelp')" @click="answer('always')">{{ t('playground.allowAlways') }}</AppButton>
        <AppButton variant="filled" :disabled="answering" @click="answer('allow')">{{ t('playground.allow') }}</AppButton>
      </div>
    </div>
  </div>
</template>

<style scoped>
.tool-call {
  display: flex; flex-direction: column; min-width: 0;
  border: 1px solid var(--md-sys-color-outline-variant); border-radius: var(--md-sys-shape-corner-medium); background: var(--md-sys-color-surface-container-low);
}
.tool-call.is-waiting { border-color: var(--md-sys-color-tertiary); }
.line {
  display: flex; align-items: center; gap: var(--app-space-2); min-height: 40px; padding: 6px var(--app-space-3); min-width: 0;
  border: 0; border-radius: inherit; background: transparent; color: var(--md-sys-color-on-surface-variant); font: inherit; text-align: start; cursor: pointer;
}
.kind { flex: none; }
.what { display: flex; align-items: baseline; gap: var(--app-space-2); flex: 1; min-width: 0; overflow: hidden; }
.verb { flex: none; color: var(--md-sys-color-on-surface); }
.detail { min-width: 0; overflow: hidden; white-space: nowrap; text-overflow: ellipsis; }
code.detail { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 0.92em; }
.state { display: inline-flex; align-items: center; gap: 4px; flex: none; }
.state.done { color: var(--md-sys-color-primary); }
.state.failed { color: var(--md-sys-color-error); }
.state.ask { color: var(--md-sys-color-tertiary); }
.spin { animation: tool-spin 1s linear infinite; }
@keyframes tool-spin { to { transform: rotate(360deg); } }
.chevron { flex: none; transition: transform var(--md-sys-motion-duration-short4) var(--md-sys-motion-easing-standard); }
.open .chevron { transform: rotate(180deg); }
.body { display: flex; flex-direction: column; gap: var(--app-space-2); padding: 0 var(--app-space-3) var(--app-space-3); }
/* Commands, patches and output wrap rather than scroll sideways; long output scrolls down. */
.block {
  margin: 0; padding: var(--app-space-2) var(--app-space-3); border-radius: var(--md-sys-shape-corner-small);
  background: var(--md-sys-color-surface-container-highest); color: var(--md-sys-color-on-surface);
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace; white-space: pre-wrap; overflow-wrap: anywhere;
}
.output { max-height: 320px; }
.output.failed { color: var(--md-sys-color-error); }
.prompt { color: var(--md-sys-color-on-surface-variant); user-select: none; }
.patch .add { color: var(--md-sys-color-primary); }
.patch .remove { color: var(--md-sys-color-error); }
.patch .head { color: var(--md-sys-color-on-surface-variant); font-weight: 600; }
.approval {
  display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: var(--app-space-2);
  padding: var(--app-space-2) var(--app-space-2) var(--app-space-2) var(--app-space-3); border-top: 1px solid var(--md-sys-color-outline-variant);
}
.question { display: inline-flex; align-items: center; gap: var(--app-space-2); color: var(--md-sys-color-on-surface); }
.question :deep(svg) { flex: none; color: var(--md-sys-color-tertiary); }
.answers { display: flex; flex-wrap: wrap; justify-content: flex-end; gap: var(--app-space-1); margin-inline-start: auto; }
@media (prefers-reduced-motion: reduce) {
  .spin { animation: none; }
  .chevron { transition: none; }
}
</style>
