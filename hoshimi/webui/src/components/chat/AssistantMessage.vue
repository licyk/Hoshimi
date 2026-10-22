<script setup lang="ts">
import { computed } from 'vue';
import type { ApprovalDecision, MessageView, Notice, ToolStep } from '@/api/types';
import ActionButton from '@/components/chat/ActionButton.vue';
import ReasoningSection from '@/components/chat/ReasoningSection.vue';
import ToolCall from '@/components/chat/ToolCall.vue';
import VersionSwitcher from '@/components/chat/VersionSwitcher.vue';
import { useI18n } from '@/i18n';
import { stepsOf, type Stage } from '@/stores/chat';
import { AppButton, AppIcon, AppMenu, icons } from '@/ui';
// Not through '@/ui': markdown-it and highlight.js load with the Playground alone.
import Markdown from '@/ui/Markdown.vue';

/**
 * A reply, across the column with no bubble and no name: what it reasoned, folded away (only for a
 * model that shows its reasoning), the Markdown it said, what is happening while it has said nothing
 * yet (reading an image, waiting for the model), how it ended when it did not end
 * well, and what the reader should know about how it was made (an image described by another
 * model). A reply that used tools is its steps in order: what it reasoned, said and called, each
 * call a line that opens (and asks for approval when it must), and what the user said to it while
 * it ran, where it read it. Under it: Copy, Try again (with the model now in the composer), Branch in a new chat, the
 * way between its versions, and More (Copy as API request). The latest reply keeps its actions in view.
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
// Reasoning has its own line ("Thinking…"); the waiting line covers the time before anything comes,
// and the time between a round's calls and the model's next words.
const waiting = computed(() => {
  if (!streaming.value) return '';
  const last = steps.value[steps.value.length - 1];
  if (last) return last.type === 'tool' && SETTLED.has(last.status) ? t('playground.working') : '';
  if (props.message.content || props.message.reasoning) return '';
  if (props.stage?.kind === 'describing') return t('playground.describing', { model: props.stage.model });
  return t('playground.answering');
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
    <div v-if="waiting" class="waiting type-body-medium muted" role="status"><span class="dot" aria-hidden="true" />{{ waiting }}</div>
    <p v-if="message.status === 'stopped'" class="ended type-body-small muted">{{ t('playground.stopped') }}</p>
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
.waiting { display: flex; align-items: center; gap: var(--app-space-2); min-height: 28px; }
.dot { width: 10px; height: 10px; border-radius: 50%; background: var(--md-sys-color-primary); animation: waiting-pulse 1.2s var(--md-sys-motion-easing-standard) infinite; }
@keyframes waiting-pulse { 0%, 100% { opacity: 0.35; transform: scale(0.8); } 50% { opacity: 1; transform: scale(1); } }
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
@media (prefers-reduced-motion: reduce) { .dot { animation: none; } }
</style>
