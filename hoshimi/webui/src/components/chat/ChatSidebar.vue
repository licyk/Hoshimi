<script setup lang="ts">
import { computed, ref, watch } from 'vue';
import { useConversations } from '@/api/conversations';
import type { ConversationSummary } from '@/api/types';
import { useI18n } from '@/i18n';
import { AppIcon, AppMenu, EmptyState, IconButton, SearchField, Skeleton, icons } from '@/ui';

export type ConversationAction = 'rename' | 'archive' | 'unarchive' | 'delete';

/**
 * The Playground's chats, as in a chat app's sidebar: a search through the titles, and the chats
 * grouped by when they were last used (today, yesterday, the last week, the last month,
 * before). Each has a menu to rename, archive (or restore) and delete it; the page does those, so
 * the same dialogs serve the header's menu. The archive is a view of its own, at the foot.
 */
const props = defineProps<{ current: string | null; refresh?: number }>();
const emit = defineEmits<{ select: [id: string]; act: [action: ConversationAction, conversation: ConversationSummary] }>();
const { t } = useI18n();

const archived = ref(false);
const list = useConversations(archived);
watch(() => props.refresh, () => void list.refetch());
const query = ref('');
const all = computed(() => list.data.value?.conversations ?? []);
const matching = computed(() => {
  const q = query.value.trim().toLowerCase();
  return q ? all.value.filter((c) => (c.title || t('playground.untitled')).toLowerCase().includes(q)) : all.value;
});

const DAY = 86_400_000;
/** The chats in groups by when they were last used, in the order shown; an empty group is left out. */
const groups = computed(() => {
  const midnight = new Date();
  midnight.setHours(0, 0, 0, 0);
  const start = midnight.getTime();
  const buckets: { key: string; label: string; from: number }[] = [
    { key: 'today', label: t('playground.today'), from: start },
    { key: 'yesterday', label: t('playground.yesterday'), from: start - DAY },
    { key: 'week', label: t('playground.week'), from: start - 7 * DAY },
    { key: 'month', label: t('playground.month'), from: start - 30 * DAY },
    { key: 'older', label: t('playground.older'), from: -Infinity },
  ];
  return buckets
    .map((b, i) => ({ ...b, items: matching.value.filter((c) => c.updated_at * 1000 >= b.from && (i === 0 || c.updated_at * 1000 < buckets[i - 1].from)) }))
    .filter((g) => g.items.length);
});
const menu = (c: ConversationSummary) => [
  { id: 'rename', label: t('playground.rename'), icon: icons.Pencil },
  c.archived ? { id: 'unarchive', label: t('playground.unarchive'), icon: icons.ArchiveRestore } : { id: 'archive', label: t('playground.archive'), icon: icons.Archive },
  { id: 'delete', label: t('playground.delete'), icon: icons.Trash2, danger: true },
];
const titleOf = (c: ConversationSummary) => c.title || t('playground.untitled');
</script>

<template>
  <nav class="chat-sidebar" :aria-label="t('playground.chats')">
    <SearchField v-if="!archived" v-model="query" compact class="search" :label="t('playground.searchChats')" :placeholder="t('playground.searchChats')" />
    <button v-else type="button" class="back state-layer type-label-large" @click="archived = false">
      <AppIcon :icon="icons.ArrowLeft" :size="18" />{{ t('playground.backToChats') }}
    </button>

    <div class="list app-scroll app-scroll-auto">
      <div v-if="list.isPending.value" class="skeletons"><Skeleton v-for="i in 5" :key="i" height="36px" shape="medium" /></div>
      <EmptyState
        v-else-if="!all.length"
        :icon="archived ? icons.Archive : icons.MessagesSquare"
        :title="archived ? t('playground.noArchived') : t('playground.noConversations')"
        :text="archived ? t('playground.noArchivedText') : t('playground.noConversationsText')"
      />
      <p v-else-if="!matching.length" class="none type-body-medium muted">{{ t('playground.noMatches', { query: query.trim() }) }}</p>
      <section v-for="g in groups" :key="g.key" class="group" :aria-label="g.label">
        <h3 class="label type-label-medium muted">{{ g.label }}</h3>
        <ul class="items">
          <li v-for="c in g.items" :key="c.id" class="item" :class="{ current: c.id === current }">
            <button type="button" class="open state-layer" :aria-current="c.id === current ? 'page' : undefined" :title="titleOf(c)" @click="emit('select', c.id)">
              <AppIcon v-if="c.branched_from" :icon="icons.GitBranch" :size="18" class="branched" :label="t('playground.branchedFrom')" />
              <span class="title type-body-medium" :class="{ muted: !c.title }">{{ titleOf(c) }}</span>
            </button>
            <AppMenu :items="menu(c)" @select="(action) => emit('act', action as ConversationAction, c)">
              <template #default="{ toggle }"><IconButton :icon="icons.MoreHorizontal" :label="t('playground.chatMenu')" class="more" @click="toggle" /></template>
            </AppMenu>
          </li>
        </ul>
      </section>
    </div>

    <button v-if="!archived" type="button" class="archive-link state-layer type-label-large" @click="archived = true">
      <AppIcon :icon="icons.Archive" :size="18" />{{ t('playground.archivedChats') }}
    </button>
  </nav>
</template>

<style scoped>
.chat-sidebar { display: flex; flex-direction: column; gap: var(--app-space-2); min-width: 0; min-height: 0; height: 100%; }
.back, .archive-link {
  display: flex; align-items: center; gap: var(--app-space-2); height: 36px; padding: 0 var(--app-space-3); border: 0; border-radius: var(--md-sys-shape-corner-full);
  background: transparent; color: var(--md-sys-color-on-surface-variant); cursor: pointer; text-align: start;
}
.archive-link { flex: none; }
.list { flex: 1 1 auto; min-height: 0; display: flex; flex-direction: column; gap: var(--app-space-3); }
.skeletons { display: flex; flex-direction: column; gap: var(--app-space-1); }
.none { margin: var(--app-space-2) var(--app-space-3); }
.group { display: flex; flex-direction: column; gap: 2px; }
.label { margin: 0; padding: var(--app-space-1) var(--app-space-3); }
.items { display: flex; flex-direction: column; gap: 1px; margin: 0; padding: 0; list-style: none; }
.item { position: relative; display: flex; align-items: center; min-width: 0; border-radius: var(--md-sys-shape-corner-medium); }
.item.current { background: var(--md-sys-color-secondary-container); color: var(--md-sys-color-on-secondary-container); }
.open {
  display: flex; align-items: center; gap: var(--app-space-2); flex: 1; min-width: 0; height: 36px; padding: 0 var(--app-space-1) 0 var(--app-space-3);
  border: 0; border-radius: inherit; background: transparent; color: inherit; font: inherit; text-align: start; cursor: pointer;
}
.branched { flex: none; opacity: 0.7; }
.title { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
/* The menu shows where the pointer or the focus is, and on the open chat. */
.more { opacity: 0; transition: opacity var(--md-sys-motion-duration-short3) var(--md-sys-motion-easing-standard); }
.item:hover .more, .item:focus-within .more, .item.current .more { opacity: 1; }
@media (hover: none) { .more { opacity: 1; } }
</style>
