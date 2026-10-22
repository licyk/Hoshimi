<script setup lang="ts">
import { nextTick, ref, watch } from 'vue';
import type { AttachmentView, MessageView } from '@/api/types';
import ActionButton from '@/components/chat/ActionButton.vue';
import AttachmentPreview from '@/components/chat/AttachmentPreview.vue';
import VersionSwitcher from '@/components/chat/VersionSwitcher.vue';
import { useI18n } from '@/i18n';
import { AppButton, icons } from '@/ui';
import { morphHeight } from '@/ui/motion/transitions';

/**
 * A question as sent: what was attached, then the text in a bubble at the end of the line, with no
 * name beside it. Under it, on hover or focus, Copy, Edit and (once it has been edited) the way
 * between its versions. Editing happens in place, the question easing into its editor and back
 * (``morphHeight``); sending the edit adds a version beside this one and the reply to it, while
 * this one stays a version away.
 */
const props = defineProps<{ message: MessageView; index: number; total: number; editing: boolean; busy: boolean; local: boolean }>();
const emit = defineEmits<{
  copy: [];
  'edit-start': [];
  'edit-cancel': [];
  'edit-submit': [content: string, attachments: AttachmentView[]];
  previous: [];
  next: [];
}>();
const { t } = useI18n();

const draft = ref('');
const kept = ref<AttachmentView[]>([]);
const box = ref<HTMLTextAreaElement | null>(null);
const root = ref<HTMLElement | null>(null);
/** What is shown, following ``editing`` once the box has been measured for the change. */
const showEditor = ref(props.editing);
/** Whether the content shown is new (and fades in), rather than there from the start. */
const changed = ref(false);
watch(
  () => props.editing,
  async (editing) => {
    if (editing) {
      draft.value = props.message.content;
      kept.value = [...props.message.attachments];
    }
    changed.value = true;
    // The editor's text box fits its text before the box is measured for the change.
    await morphHeight(root.value, async () => {
      showEditor.value = editing;
      await nextTick();
      if (editing) fit();
    });
    if (editing) box.value?.focus();
  },
);
if (props.editing) {
  draft.value = props.message.content;
  kept.value = [...props.message.attachments];
  void nextTick(() => {
    box.value?.focus();
    fit();
  });
}
function fit() {
  const el = box.value;
  if (!el) return;
  el.style.height = 'auto';
  el.style.height = `${el.scrollHeight}px`;
}
const ready = () => !!draft.value.trim() || kept.value.length > 0;
function submit() {
  if (ready()) emit('edit-submit', draft.value.trim(), kept.value);
}
function onKeydown(e: KeyboardEvent) {
  if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) {
    e.preventDefault();
    submit();
  } else if (e.key === 'Escape') {
    e.preventDefault();
    emit('edit-cancel');
  }
}
</script>

<template>
  <div ref="root" class="user-message" :class="{ editing: showEditor }">
    <div v-if="showEditor" class="editor" :class="{ 'morph-in': changed }">
      <div v-if="kept.length" class="attachments">
        <AttachmentPreview
          v-for="a in kept"
          :key="a.id"
          :kind="a.kind"
          :name="a.name"
          :mime="a.mime"
          :bytes="a.size"
          :id="a.id"
          removable
          @remove="kept = kept.filter((k) => k.id !== a.id)"
        />
      </div>
      <textarea ref="box" v-model="draft" class="box app-scroll app-scroll-auto type-body-large" rows="1" :aria-label="t('playground.edit')" @input="fit" @keydown="onKeydown" />
      <div class="buttons">
        <AppButton variant="text" @click="emit('edit-cancel')">{{ t('common.cancel') }}</AppButton>
        <AppButton variant="filled" :disabled="busy || !draft.trim() && !kept.length" @click="submit">{{ t('playground.send') }}</AppButton>
      </div>
    </div>
    <div v-else class="view" :class="{ 'morph-in': changed }">
      <div v-if="message.attachments.length" class="attachments">
        <AttachmentPreview v-for="a in message.attachments" :key="a.id" :kind="a.kind" :name="a.name" :mime="a.mime" :bytes="a.size" :id="a.id" />
      </div>
      <div v-if="message.content" class="bubble type-body-large">{{ message.content }}</div>
      <div class="actions" :class="{ pinned: total > 1 }">
        <ActionButton :icon="icons.Copy" :label="t('playground.copyMessage')" @click="emit('copy')" />
        <ActionButton :icon="icons.Pencil" :label="t('playground.edit')" :disabled="busy || local" @click="emit('edit-start')" />
        <VersionSwitcher v-if="total > 1" :index="index" :total="total" :disabled="busy" @previous="emit('previous')" @next="emit('next')" />
      </div>
    </div>
  </div>
</template>

<style scoped>
.user-message { display: flex; flex-direction: column; align-items: flex-end; min-width: 0; }
.view { display: flex; flex-direction: column; align-items: flex-end; align-self: stretch; gap: var(--app-space-1); min-width: 0; }
.attachments { display: flex; flex-wrap: wrap; justify-content: flex-end; gap: var(--app-space-2); max-width: min(560px, 100%); }
.bubble {
  max-width: min(560px, 85%); min-width: 0; padding: var(--app-space-2) var(--app-space-4); border-radius: var(--md-sys-shape-corner-extra-large);
  background: var(--md-sys-color-surface-container-high); color: var(--md-sys-color-on-surface); white-space: pre-wrap; overflow-wrap: anywhere;
}
/* The actions show where the pointer or the focus is; a turn with versions keeps its way between them in view. */
.actions { display: flex; align-items: center; gap: 2px; opacity: 0; transition: opacity var(--md-sys-motion-duration-short3) var(--md-sys-motion-easing-standard); }
.view:hover .actions, .actions:focus-within, .actions.pinned { opacity: 1; }
@media (hover: none) { .actions { opacity: 1; } }
.editor {
  display: flex; flex-direction: column; gap: var(--app-space-2); width: min(100%, 640px); padding: var(--app-space-3);
  border-radius: var(--md-sys-shape-corner-extra-large); background: var(--md-sys-color-surface-container-high);
}
.editor .attachments { justify-content: flex-start; max-width: none; }
.box {
  display: block; width: 100%; min-height: 48px; max-height: 50vh; margin: 0; padding: var(--app-space-1) var(--app-space-2); border: 0; outline: none;
  background: transparent; color: var(--md-sys-color-on-surface); font: inherit; resize: none;
}
.buttons { display: flex; justify-content: flex-end; gap: var(--app-space-2); }
</style>
