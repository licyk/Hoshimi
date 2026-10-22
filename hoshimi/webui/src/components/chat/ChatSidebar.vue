<script setup lang="ts">
import { computed, ref, watch } from 'vue';
import { useConversations } from '@/api/conversations';
import type { ConversationSummary } from '@/api/types';
import ChatList, { type ConversationAction } from '@/components/chat/ChatList.vue';
import { useI18n } from '@/i18n';
import { AppIcon, SearchField, TRANSITIONS, icons } from '@/ui';

export type { ConversationAction };

/**
 * The Playground's chats, as in a chat app's sidebar: a search through the titles and the chats
 * grouped by when they were last used, with the archive as a view of its own at the foot. Opening
 * the archive slides it in from the end, as going into a folder, and going back slides the other
 * way (shared-axis-x); each view keeps its own list, so the one leaving does not change under the
 * slide. What is done to a chat (rename, archive, restore, delete) is the page's.
 */
const props = defineProps<{ current: string | null; refresh?: number }>();
const emit = defineEmits<{ select: [id: string]; act: [action: ConversationAction, conversation: ConversationSummary] }>();
const { t } = useI18n();

const archived = ref(false);
const recent = useConversations(ref(false));
const archive = useConversations(ref(true));
watch(() => props.refresh, () => void Promise.all([recent.refetch(), archive.refetch()]));
const query = ref('');
const recentChats = computed(() => recent.data.value?.conversations ?? []);
const archivedChats = computed(() => archive.data.value?.conversations ?? []);
// Into the archive is forward; back to the chats, backward.
const axis = computed(() => (archived.value ? 1 : -1));
</script>

<template>
  <nav class="chat-sidebar" :aria-label="t('playground.chats')" :style="{ '--axis-dir': axis }">
    <Transition :name="TRANSITIONS.sharedAxisX">
      <div v-if="!archived" key="recent" class="pane">
        <SearchField v-model="query" compact class="search" :label="t('playground.searchChats')" :placeholder="t('playground.searchChats')" />
        <ChatList :conversations="recentChats" :pending="recent.isPending.value" :current="current" :query="query" @select="(id) => emit('select', id)" @act="(a, c) => emit('act', a, c)" />
        <button type="button" class="archive-link state-layer type-label-large" @click="archived = true">
          <AppIcon :icon="icons.Archive" :size="18" />{{ t('playground.archivedChats') }}
        </button>
      </div>
      <div v-else key="archive" class="pane">
        <button type="button" class="back state-layer type-label-large" @click="archived = false">
          <AppIcon :icon="icons.ArrowLeft" :size="18" />{{ t('playground.backToChats') }}
        </button>
        <ChatList :conversations="archivedChats" :pending="archive.isPending.value" :current="current" archived @select="(id) => emit('select', id)" @act="(a, c) => emit('act', a, c)" />
      </div>
    </Transition>
  </nav>
</template>

<style scoped>
/* The positioned box the leaving view slides out of (shared-axis-x takes it out of the flow), clipped so the slide never scrolls the page. */
.chat-sidebar { position: relative; display: flex; flex-direction: column; min-width: 0; min-height: 0; height: 100%; overflow-x: clip; }
.pane { display: flex; flex-direction: column; gap: var(--app-space-2); min-width: 0; min-height: 0; height: 100%; }
/* The leaving view keeps the whole height, wherever it sits among the two while they cross. */
.pane.shared-axis-x-leave-active { inset-block: 0; height: auto; }
.back, .archive-link {
  display: flex; flex: none; align-items: center; gap: var(--app-space-2); height: 36px; padding: 0 var(--app-space-3); border: 0; border-radius: var(--md-sys-shape-corner-full);
  background: transparent; color: var(--md-sys-color-on-surface-variant); cursor: pointer; text-align: start;
}
</style>
