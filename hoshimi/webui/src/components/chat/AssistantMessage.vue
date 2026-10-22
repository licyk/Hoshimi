<script setup lang="ts">
import { computed } from 'vue';
import type { ApprovalDecision, MessageView, Notice, ToolStep } from '@/api/types';
import ActionButton from '@/components/chat/ActionButton.vue';
import CompactionMarker from '@/components/chat/CompactionMarker.vue';
import ReasoningSection from '@/components/chat/ReasoningSection.vue';
import RunStatus from '@/components/chat/RunStatus.vue';
import ToolCall from '@/components/chat/ToolCall.vue';
import VersionSwitcher from '@/components/chat/VersionSwitcher.vue';
import { elapsed } from '@/format';
import { useI18n } from '@/i18n';
import { stepsOf, type Stage } from '@/stores/chat';
import { AppButton, AppIcon, AppMenu, Tooltip, icons } from '@/ui';
// Not through '@/ui': markdown-it and highlight.js load with the Playground alone.
import Markdown from '@/ui/Markdown.vue';

/**
 * A reply, across the column with no bubble and no name: what it reasoned, folded away (only for a
 * model that shows its reasoning), the Markdown it said, what is happening while it has said nothing
 * yet (reading an image, waiting for the model), how it ended when it did not end
 * well, and what the reader should know about how it was made (an image described by another
 * model). A reply that used tools is its steps in order: what it reasoned, said and called, each
 * call a line that opens (and asks for approval when it must), and what the user said to it while
 * it ran, where it read it, and where the conversation was compacted. While it comes in, a line at its foot says what it
 * is doing and for how long (from ``startedAt``, by this page's clock); once it ends, how long it worked. Under it: Copy,
 * Try again (with the model now in the composer), Branch in a new chat, the way between its versions, and More (Copy as
 * API request). The latest reply keeps its actions in view.
 */
const props = defineProps<{
  message: MessageView;
  index: number;
  total: number;
  busy: boolean;
  last: boolean;
  /** Before the first word of the reply coming in. */
  stage: Stage;
  local: boolean;
  temporary: boolean;
  /** When the reply under way started (milliseconds, this page's clock); 0 to count from when it was filed. */
  startedAt?: number;
}>();
const emit = defineEmits<{
  copy: []; retry: []; branch: []; 'copy-request': []; previous: []; next: []; copied: []; 'copy-failed': []; approve: [ToolStep, ApprovalDecision];
}>();
const { t } = useI18n();

const streaming = computed(() => props.message.status === 'streaming');
const steps = computed(() => stepsOf(props.message));
/** Step by step while it streams (it may call a tool yet), and kept so when it did. */
const timeline = computed(() => steps.value.length > 0);
const SETTLED = new Set(['done', 'error', 'denied', 'stopped']);
// What the reply under way is doing now, for the line at its foot: reading an image, compacting,
// waiting for approval (its clock stopped), running a tool, thinking, answering, or between rounds.
const waitingForApproval = computed(() => steps.value.some((s) => s.type === 'tool' && s.status === 'waiting'));
const doing = computed(() => {
  if (!streaming.value) return '';
  const last = steps.value[steps.value.length - 1];
  if (!last && props.stage?.kind === 'describing') return t('playground.describing', { model: props.stage.model });
  if (waitingForApproval.value) return t('playground.waitingApproval');
  if (last?.type === 'compaction' && last.status === 'running') return t('playground.compacting');
  const running = steps.value.find((s) => s.type === 'tool' && s.status === 'running');
  if (running?.type === 'tool') return t('playground.runningTool', { name: running.name });
  if (last?.type === 'tool' && SETTLED.has(last.status)) return t('playground.working');
  // Reasoning says "Thinking…" on its own line, with its own clock: this one only says the reply works on.
  if (last?.type === 'reasoning' || (!last && props.message.reasoning && !props.message.content)) return t('playground.working');
  return props.message.content || last ? t('playground.writing') : t('playground.answering');
});
const startedAt = computed(() => props.startedAt || props.message.created_at * 1000);
// Once it ends: how long it worked (the time it waited for approval left out), with when it ran in a tooltip.
const worked = computed(() => {
  const m = props.message;
  if (streaming.value || !((m.duration_ms ?? 0) >= 1000)) return '';
  const time = elapsed(m.duration_ms);
  return m.status === 'stopped' ? t('playground.stoppedAfter', { time }) : m.status === 'error' ? t('playground.failedAfter', { time }) : t('playground.workedFor', { time });
});
const workedDetail = computed(() => {
  const m = props.message;
  const clock = (s: number) => new Date(s * 1000).toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit', second: '2-digit' });
  const parts = [t('playground.ranFrom', { start: clock(m.created_at), end: clock(m.finished_at || m.created_at) })];
  if ((m.waited_ms ?? 0) >= 1000) parts.push(t('playground.waitedFor', { time: elapsed(m.waited_ms) }));
  return parts.join(' · ');
});
const noticeText = (n: Notice) =>
  n.kind === 'image-described' ? t('playground.noticeDescribed', { name: n.name, model: n.model })
  : n.kind === 'image-refused' ? t('playground.noticeRefused', { model: n.model })
  : n.kind === 'tools-unsupported' ? t('playground.noticeNoTools', { model: n.model })
  : n.kind === 'step-limit' ? t('playground.noticeStepLimit', { n: n.name })
  : t('playground.noticeOmitted', { name: n.name });
const noticeIcon = (n: Notice) =>
  n.kind === 'image-described' ? icons.Image : n.kind === 'tools-unsupported' ? icons.Wrench : n.kind === 'step-limit' ? icons.AlertTriangle : icons.ImageOff;
const more = computed(() => [{ id: 'request', label: t('playground.copyRequest'), icon: icons.FileText }]);
const failure = computed(() => [t('playground.replyFailed'), props.message.error].filter(Boolean).join(' '));
</script>

<template>
  <div class="assistant-message" :class="{ last, streaming }">
    <template v-if="timeline">
      <template v-for="(s, i) in steps" :key="s.type === 'tool' ? s.id : `${s.type}-${i}`">
        <ReasoningSection v-if="s.type === 'reasoning'" :reasoning="s.text" :ms="s.ms" :active="streaming && i === steps.length - 1" />
        <Markdown
          v-else-if="s.type === 'text'"
          class="content type-body-large"
          :source="s.text"
          :copy-label="t('playground.copyCode')"
          :copied-label="t('playground.codeCopied')"
          @copied="emit('copied')"
          @copy-failed="emit('copy-failed')"
        />
        <p v-else-if="s.type === 'user'" class="steered type-body-large">{{ s.text }}</p>
        <CompactionMarker v-else-if="s.type === 'compaction'" :step="s" :settled="!streaming" />
        <ToolCall v-else :step="s" @approve="(decision) => emit('approve', s, decision)" />
      </template>
    </template>
    <template v-else>
      <ReasoningSection v-if="message.reasoning" :reasoning="message.reasoning" :ms="message.reasoning_ms" :active="streaming && !message.content" />
      <Markdown
        v-if="message.content"
        class="content type-body-large"
        :source="message.content"
        :copy-label="t('playground.copyCode')"
        :copied-label="t('playground.codeCopied')"
        @copied="emit('copied')"
        @copy-failed="emit('copy-failed')"
      />
    </template>
    <RunStatus v-if="streaming" :label="doing" :started-at="startedAt" :paused="waitingForApproval" :hint="last ? t('playground.escToStop') : ''" />
    <p v-if="message.status === 'stopped' && !worked" class="ended type-body-small muted">{{ t('playground.stopped') }}</p>
    <div v-if="message.status === 'error'" class="failed type-body-medium" role="alert">
      <AppIcon :icon="icons.AlertTriangle" :size="18" />
      <span class="reason">{{ failure }}</span>
      <AppButton variant="text" :icon="icons.RotateCcw" :disabled="busy || local" @click="emit('retry')">{{ t('playground.retry') }}</AppButton>
    </div>
    <ul v-if="message.notices.length" class="notices type-body-small muted">
      <li v-for="(n, i) in message.notices" :key="i"><AppIcon :icon="noticeIcon(n)" :size="18" />{{ noticeText(n) }}</li>
    </ul>
    <div v-if="!streaming" class="actions" :class="{ pinned: last || total > 1 }">
      <ActionButton v-if="message.content" :icon="icons.Copy" :label="t('playground.copyMessage')" @click="emit('copy')" />
      <ActionButton :icon="icons.RotateCcw" :label="t('playground.retry')" :disabled="busy || local" @click="emit('retry')" />
      <ActionButton v-if="!temporary" :icon="icons.GitBranch" :label="t('playground.branch')" :disabled="busy || local" @click="emit('branch')" />
      <VersionSwitcher v-if="total > 1" :index="index" :total="total" :disabled="busy" @previous="emit('previous')" @next="emit('next')" />
      <AppMenu v-if="!local" :items="more" @select="emit('copy-request')">
        <template #default="{ toggle }"><ActionButton :icon="icons.MoreHorizontal" :label="t('playground.moreActions')" @click="toggle" /></template>
      </AppMenu>
      <Tooltip v-if="worked" :text="workedDetail">
        <span class="worked type-body-small" tabindex="0"><AppIcon :icon="icons.Timer" :size="18" />{{ worked }}</span>
      </Tooltip>
    </div>
  </div>
</template>

<style scoped>
.assistant-message { display: flex; flex-direction: column; gap: var(--app-space-2); min-width: 0; }
.content { color: var(--md-sys-color-on-surface); }
/* What the user said while it ran, where the model read it: a question's bubble, inside the reply. */
.steered {
  align-self: flex-end; max-width: min(85%, 560px); margin: var(--app-space-1) 0; padding: var(--app-space-2) var(--app-space-4);
  border-radius: 20px; background: var(--md-sys-color-surface-container-high); color: var(--md-sys-color-on-surface); white-space: pre-wrap; overflow-wrap: anywhere;
}
.ended { margin: 0; }
.failed {
  display: flex; align-items: center; flex-wrap: wrap; gap: var(--app-space-2); padding: var(--app-space-2) var(--app-space-2) var(--app-space-2) var(--app-space-3);
  border-radius: var(--md-sys-shape-corner-medium); background: var(--md-sys-color-error-container); color: var(--md-sys-color-on-error-container);
}
.reason { flex: 1; min-width: 0; overflow-wrap: anywhere; }
.notices { display: flex; flex-direction: column; gap: 2px; margin: 0; padding: 0; list-style: none; }
.notices li { display: flex; align-items: flex-start; gap: var(--app-space-1); }
.notices li :deep(svg) { flex: none; margin-top: 2px; }
.actions { display: flex; align-items: center; flex-wrap: wrap; gap: 2px; margin-inline-start: -6px; opacity: 0; transition: opacity var(--md-sys-motion-duration-short3) var(--md-sys-motion-easing-standard); }
.assistant-message:hover .actions, .actions:focus-within, .actions.pinned { opacity: 1; }
@media (hover: none) { .actions { opacity: 1; } }
/* How long it worked, after its actions: quiet, and in view with them. */
.worked {
  display: inline-flex; align-items: center; gap: 4px; margin-inline-start: var(--app-space-2); padding: 2px 6px; border-radius: var(--md-sys-shape-corner-small);
  color: var(--md-sys-color-on-surface-variant); font-variant-numeric: tabular-nums; animation: worked-in var(--md-sys-motion-duration-medium2) var(--md-sys-motion-easing-emphasized-decelerate, ease-out);
}
@keyframes worked-in { from { opacity: 0; transform: translateX(-4px); } }
@media (prefers-reduced-motion: reduce) { .worked { animation: none; } }
</style>
