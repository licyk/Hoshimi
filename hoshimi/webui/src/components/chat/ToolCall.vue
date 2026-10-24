<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch, type Component } from 'vue';
import type { ApprovalDecision, ToolStep } from '@/api/types';
import CommandView from '@/components/chat/tools/CommandView.vue';
import FetchView from '@/components/chat/tools/FetchView.vue';
import GenericView from '@/components/chat/tools/GenericView.vue';
import PatchView from '@/components/chat/tools/PatchView.vue';
import RawOutput from '@/components/chat/tools/RawOutput.vue';
import ReadView from '@/components/chat/tools/ReadView.vue';
import ReviewNote from '@/components/chat/tools/ReviewNote.vue';
import SearchView from '@/components/chat/tools/SearchView.vue';
import { hostOf, parseArguments, partialString } from '@/components/chat/tools/parse';
import { duration } from '@/format';
import { useI18n } from '@/i18n';
import { AppButton, AppIcon, Collapse, icons } from '@/ui';

/**
 * One tool call in a reply, as a line that opens: what it did in a few words ("Ran `npm test`",
 * "Edited app.py"), how it is going (preparing, being reviewed, waiting for approval, running with the
 * time so far, done, failed, declined, stopped) and how long it took. Opened, it shows what was asked,
 * what the reviewer said of it, and what came back, each laid out for its tool (``tools/``): a patch
 * as the diff of each file it changes, a file read as its numbered lines and a directory as its
 * entries, a command with its output and exit code, search results and a fetched page as such, and
 * any other tool's arguments one to a row. A call that waits for approval stands open with the question and the
 * answers under it (and why the reviewer could not decide, when it was asked first); one the reviewer
 * denied opens to say why.
 */
const props = defineProps<{ step: ToolStep }>();
const emit = defineEmits<{ approve: [ApprovalDecision] }>();
const { t } = useI18n();
const id = `tool-${Math.random().toString(36).slice(2, 10)}`;
const open = ref(false);
const answering = ref(false);
/** Opened to ask: folded again once answered and done, as the other calls are. */
let openedToAsk = false;

const args = computed(() => parseArguments(props.step.arguments));
/** A string argument, or as much of it as has come while the arguments are still coming in. */
const str = (key: string) => (typeof args.value[key] === 'string' ? (args.value[key] as string) : partialString(props.step.arguments, key));
const firstLine = (text: string) => {
  const line = text.trim().split('\n')[0] ?? '';
  return text.trim().includes('\n') ? `${line} …` : line;
};
/** The files a patch names, in order. */
const patchFiles = computed(() => [...str('input').matchAll(/^\*\*\* (?:Add|Update|Delete) File: (.+)$/gm)].map((m) => m[1].trim()));

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
      return { label, detail: hostOf(str('url')), code: false };
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
    if (status === 'denied' && before === 'reviewing' && !open.value) open.value = true;
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
    case 'reviewing':
      return { icon: icons.Loader2, text: t('playground.toolReviewing'), tone: 'busy' };
    case 'waiting':
      return { icon: icons.ShieldAlert, text: t('playground.toolWaiting'), tone: 'ask' };
    case 'running':
      return { icon: icons.Loader2, text: now.value - started.value >= 1000 ? duration(Math.floor((now.value - started.value) / 1000) * 1000) : '', tone: 'busy' };
    case 'done':
      return { icon: icons.Check, text: s.ms >= 100 ? duration(s.ms) : '', tone: 'done' };
    case 'denied':
      return s.review?.outcome === 'deny'
        ? { icon: icons.ShieldX, text: t('playground.toolDeniedByReview'), tone: 'failed' }
        : { icon: icons.Ban, text: t('playground.toolDenied'), tone: 'off' };
    case 'stopped':
      return { icon: icons.CircleStop, text: t('playground.toolStopped'), tone: 'off' };
    default:
      return { icon: icons.X, text: s.exit_code !== null && s.exit_code !== undefined ? t('playground.toolExit', { code: String(s.exit_code) }) : t('playground.toolFailed'), tone: 'failed' };
  }
});

/** What the reviewer said: allowed or denied (with the risk it saw and why), or that it could not decide. */
const review = computed(() => {
  const r = props.step.review;
  if (!r) return null;
  if (r.failed) return { icon: icons.ShieldAlert, tone: 'ask', text: t('playground.reviewFailed'), why: r.rationale, risk: '' };
  const risk = r.risk_level ? t(`playground.reviewRisk.${r.risk_level}`) : '';
  return r.outcome === 'allow'
    ? { icon: icons.ShieldCheck, tone: 'done', text: t('playground.reviewAllowed', { model: r.model }), why: r.rationale, risk }
    : { icon: icons.ShieldX, tone: 'failed', text: t('playground.reviewDenied', { model: r.model }), why: r.rationale, risk };
});
/** The words a model is told of a denial are the reviewer's: shown as its verdict, not as output. */
const output = computed(() => (props.step.status === 'denied' && props.step.review?.outcome === 'deny' ? '' : props.step.output));

const COMMANDS = new Set(['shell', 'exec_command', 'write_stdin', 'kill_session']);
const failed = computed(() => props.step.status === 'error');
/** What the patch tool said once it worked: the diff shows it, so only a failure is shown. */
const patchError = computed(() => (props.step.name === 'apply_patch' && failed.value ? output.value : ''));
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
        <template v-if="step.name === 'apply_patch'">
          <PatchView v-if="str('input')" :input="str('input')" :meta="step.meta" />
          <ReviewNote v-if="review && step.status !== 'waiting'" :review="review" />
          <RawOutput v-if="patchError" :text="patchError" failed />
        </template>
        <CommandView v-else-if="COMMANDS.has(step.name)" :name="step.name" :raw="step.arguments" :args="args" :output="output" :meta="step.meta" :failed="failed">
          <template #review><ReviewNote v-if="review && step.status !== 'waiting'" :review="review" /></template>
        </CommandView>
        <template v-else-if="step.name === 'read' || step.name === 'web_search' || step.name === 'web_fetch'">
          <ReviewNote v-if="review && step.status !== 'waiting'" :review="review" />
          <ReadView v-if="step.name === 'read'" :path="str('path')" :output="output" :meta="step.meta" :failed="failed" />
          <SearchView v-else-if="step.name === 'web_search'" :output="output" :failed="failed" />
          <FetchView v-else :output="output" :failed="failed" />
        </template>
        <GenericView v-else :raw="step.arguments" :args="args" :output="output" :failed="failed" :pending="step.status === 'pending'">
          <template #review><ReviewNote v-if="review && step.status !== 'waiting'" :review="review" /></template>
        </GenericView>
      </div>
    </Collapse>
    <div v-if="step.status === 'waiting'" class="approval" role="group" :aria-label="question">
      <span class="type-body-medium question"><AppIcon :icon="icons.ShieldAlert" :size="18" />{{ question }}</span>
      <span v-if="review" class="type-body-small muted why">{{ review.text }}<template v-if="review.why"> — {{ review.why }}</template></span>
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
/* What it asked and what came back stand a step under the line, as far apart as from each other. */
.body { display: flex; flex-direction: column; gap: var(--app-space-2); padding: var(--app-space-2) var(--app-space-3) var(--app-space-3); }
.approval {
  display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: var(--app-space-2);
  padding: var(--app-space-2) var(--app-space-2) var(--app-space-2) var(--app-space-3); border-top: 1px solid var(--md-sys-color-outline-variant);
}
.question { display: inline-flex; align-items: center; gap: var(--app-space-2); color: var(--md-sys-color-on-surface); }
.question :deep(svg) { flex: none; color: var(--md-sys-color-tertiary); }
.why { flex-basis: 100%; order: 1; color: var(--md-sys-color-on-surface-variant); }
.answers { order: 2; }
.answers { display: flex; flex-wrap: wrap; justify-content: flex-end; gap: var(--app-space-1); margin-inline-start: auto; }
@media (prefers-reduced-motion: reduce) {
  .spin { animation: none; }
  .chevron { transition: none; }
}
</style>
