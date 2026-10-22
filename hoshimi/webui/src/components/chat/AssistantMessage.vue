<script setup lang="ts">
import { computed } from 'vue';
import type { MessageView, Notice } from '@/api/types';
import ActionButton from '@/components/chat/ActionButton.vue';
import ReasoningSection from '@/components/chat/ReasoningSection.vue';
import VersionSwitcher from '@/components/chat/VersionSwitcher.vue';
import { useI18n } from '@/i18n';
import type { Stage } from '@/stores/chat';
import { AppButton, AppIcon, AppMenu, icons } from '@/ui';
// Not through '@/ui': markdown-it and highlight.js load with the Playground alone.
import Markdown from '@/ui/Markdown.vue';

/**
 * A reply, across the column with no bubble and no name: what it reasoned, folded away (only for a
 * model that shows its reasoning), the Markdown it said, what is happening while it has said nothing
 * yet (reading an image, waiting for the model), how it ended when it did not end
 * well, and what the reader should know about how it was made (an image described by another
 * model). Under it: Copy, Try again (with the model now in the composer), Branch in a new chat, the
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
const emit = defineEmits<{ copy: []; retry: []; branch: []; 'copy-request': []; previous: []; next: []; copied: []; 'copy-failed': [] }>();
const { t } = useI18n();

const streaming = computed(() => props.message.status === 'streaming');
// Reasoning has its own line ("Thinking…"); the waiting line covers the time before anything comes.
const waiting = computed(() => {
  if (!streaming.value || props.message.content || props.message.reasoning) return '';
  if (props.stage?.kind === 'describing') return t('playground.describing', { model: props.stage.model });
  return t('playground.answering');
});
const noticeText = (n: Notice) =>
  n.kind === 'image-described' ? t('playground.noticeDescribed', { name: n.name, model: n.model })
  : n.kind === 'image-refused' ? t('playground.noticeRefused', { model: n.model })
  : t('playground.noticeOmitted', { name: n.name });
const more = computed(() => [{ id: 'request', label: t('playground.copyRequest'), icon: icons.FileText }]);
const failure = computed(() => [t('playground.replyFailed'), props.message.error].filter(Boolean).join(' '));
</script>

<template>
  <div class="assistant-message" :class="{ last, streaming }">
    <ReasoningSection v-if="message.reasoning" :reasoning="message.reasoning" :ms="message.reasoning_ms" :active="streaming && !message.content" />
    <div v-if="waiting" class="waiting type-body-medium muted" role="status"><span class="dot" aria-hidden="true" />{{ waiting }}</div>
    <Markdown
      v-if="message.content"
      class="content type-body-large"
      :source="message.content"
      :copy-label="t('playground.copyCode')"
      :copied-label="t('playground.codeCopied')"
      @copied="emit('copied')"
      @copy-failed="emit('copy-failed')"
    />
    <p v-if="message.status === 'stopped'" class="ended type-body-small muted">{{ t('playground.stopped') }}</p>
    <div v-if="message.status === 'error'" class="failed type-body-medium" role="alert">
      <AppIcon :icon="icons.AlertTriangle" :size="18" />
      <span class="reason">{{ failure }}</span>
      <AppButton variant="text" :icon="icons.RotateCcw" :disabled="busy || local" @click="emit('retry')">{{ t('playground.retry') }}</AppButton>
    </div>
    <ul v-if="message.notices.length" class="notices type-body-small muted">
      <li v-for="(n, i) in message.notices" :key="i"><AppIcon :icon="n.kind === 'image-described' ? icons.Image : icons.ImageOff" :size="18" />{{ noticeText(n) }}</li>
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
