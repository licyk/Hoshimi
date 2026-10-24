<script setup lang="ts">
import { computed } from 'vue';
import type { ConversationSummary } from '@/api/types';
import { useI18n } from '@/i18n';
import { AppIcon, AppMenu, EmptyState, IconButton, Skeleton, TRANSITIONS, icons, keepLeavingInPlace } from '@/ui';

export type ConversationAction = 'rename' | 'archive' | 'unarchive' | 'delete';

/**
 * One list of chats (the recent ones, or the archive), grouped by when they were last used: today,
 * yesterday, the last week, the last month, before. Each has a menu to rename, archive (or restore)
 * and delete it; the page does those. A chat archived, restored or deleted fades out where it was
 * while the ones under it slide up, and a group left empty goes the same way. A chat whose reply
 * works now (here or in the background) says so after its title, and one whose tool call waits for
 * an answer says that instead.
 */
const props = defineProps<{ conversations: ConversationSummary[]; pending: boolean; current: string | null; working?: string | null; archived?: boolean; query?: string }>();
const emit = defineEmits<{ select: [id: string]; act: [action: ConversationAction, conversation: ConversationSummary] }>();
const { t } = useI18n();

const titleOf = (c: ConversationSummary) => c.title || t('playground.untitled');
/** What a chat is doing now: waiting for an answer, working, or nothing. */
const activity = (c: ConversationSummary) => (c.waiting ? 'waiting' : c.running || c.id === props.working ? 'working' : '');
const matching = computed(() => {
  const q = (props.query ?? '').trim().toLowerCase();
  return q ? props.conversations.filter((c) => titleOf(c).toLowerCase().includes(q)) : props.conversations;
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
</script>

<template>
  <div class="chat-list app-scroll app-scroll-auto">
    <div v-if="pending" class="skeletons"><Skeleton v-for="i in 5" :key="i" height="36px" shape="medium" /></div>
    <EmptyState
      v-else-if="!conversations.length"
      :icon="archived ? icons.Archive : icons.MessagesSquare"
      :title="archived ? t('playground.noArchived') : t('playground.noConversations')"
      :text="archived ? t('playground.noArchivedText') : t('playground.noConversationsText')"
    />
    <p v-else-if="!matching.length" class="none type-body-medium muted">{{ t('playground.noMatches', { query: (query ?? '').trim() }) }}</p>
    <TransitionGroup :name="TRANSITIONS.list" tag="div" class="groups" v-bind="keepLeavingInPlace">
      <section v-for="g in groups" :key="g.key" class="group" :aria-label="g.label">
        <h3 class="label type-label-medium muted">{{ g.label }}</h3>
        <TransitionGroup :name="TRANSITIONS.list" tag="ul" class="items" v-bind="keepLeavingInPlace">
          <li v-for="c in g.items" :key="c.id" class="item" :class="{ current: c.id === current }">
            <button type="button" class="open state-layer" :aria-current="c.id === current ? 'page' : undefined" :title="titleOf(c)" @click="emit('select', c.id)">
              <AppIcon v-if="c.branched_from" :icon="icons.GitBranch" :size="18" class="branched" :label="t('playground.branchedFrom')" />
              <span class="title type-body-medium" :class="{ muted: !c.title }">{{ titleOf(c) }}</span>
              <AppIcon v-if="activity(c) === 'waiting'" :icon="icons.ShieldAlert" :size="18" class="activity waiting" :label="t('playground.toolWaiting')" />
              <AppIcon v-else-if="activity(c) === 'working'" :icon="icons.Loader2" :size="18" spin class="activity working" :label="t('playground.working')" />
            </button>
            <AppMenu :items="menu(c)" @select="(action) => emit('act', action as ConversationAction, c)">
              <template #default="{ toggle }"><IconButton :icon="icons.MoreHorizontal" :label="t('playground.chatMenu')" class="more" @click="toggle" /></template>
            </AppMenu>
          </li>
        </TransitionGroup>
      </section>
    </TransitionGroup>
  </div>
</template>

<style scoped>
.chat-list { flex: 1 1 auto; min-height: 0; display: flex; flex-direction: column; gap: var(--app-space-3); }
.skeletons { display: flex; flex-direction: column; gap: var(--app-space-1); }
.none { margin: var(--app-space-2) var(--app-space-3); }
/* The positioned parents a leaving group or chat is pinned in while the rest slide up. */
.groups { position: relative; display: flex; flex-direction: column; gap: var(--app-space-3); }
.group { display: flex; flex-direction: column; gap: 2px; }
.label { margin: 0; padding: var(--app-space-1) var(--app-space-3); }
.items { position: relative; display: flex; flex-direction: column; gap: 1px; margin: 0; padding: 0; list-style: none; }
.item { position: relative; display: flex; align-items: center; min-width: 0; border-radius: var(--md-sys-shape-corner-medium); }
/* A leaving chat (or group) is pinned where it was, out of the flow: its own position must not win over the list's. */
.item.list-leave-active, .group.list-leave-active { position: absolute; }
.item.current { background: var(--md-sys-color-secondary-container); color: var(--md-sys-color-on-secondary-container); }
.open {
  display: flex; align-items: center; gap: var(--app-space-2); flex: 1; min-width: 0; height: 36px; padding: 0 var(--app-space-1) 0 var(--app-space-3);
  border: 0; border-radius: inherit; background: transparent; color: inherit; font: inherit; text-align: start; cursor: pointer;
}
.branched { flex: none; opacity: 0.7; }
.title { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
/* After the title, out of the way of the menu: a chat working now, or waiting for an answer. */
.activity { flex: none; width: 16px; height: 16px; margin-inline-start: auto; }
.activity.working { color: var(--md-sys-color-primary); }
.activity.waiting { color: var(--md-sys-color-tertiary); }
@media (prefers-reduced-motion: reduce) { .activity.working { animation-duration: 3s; } }
/* The menu shows where the pointer or the focus is, and on the open chat. */
.more { opacity: 0; transition: opacity var(--md-sys-motion-duration-short3) var(--md-sys-motion-easing-standard); }
.item:hover .more, .item:focus-within .more, .item.current .more { opacity: 1; }
@media (hover: none) { .more { opacity: 1; } }
</style>
