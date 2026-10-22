<script setup lang="ts">
import { computed, ref } from 'vue';
import { conversationsApi, useConversations, useRefreshConversations } from '@/api/conversations';
import type { ConversationSummary } from '@/api/types';
import { relative } from '@/format';
import { useI18n } from '@/i18n';
import { AppButton, AppMenu, ConfirmDialog, EmptyState, IconButton, SegmentedControl, Skeleton, icons, useSnackbar } from '@/ui';

/**
 * The Playground's kept conversations: the recent ones or the archive, the most recently used
 * first. Choosing one opens it (``select``); each has a menu to archive (or restore) and to delete,
 * which asks first. The page hears of a deletion (``deleted``) so it can leave a conversation that
 * no longer exists. Shown beside the conversation on a wide page, in a side sheet on a narrow one.
 */
const props = defineProps<{ current: string | null }>();
const emit = defineEmits<{ select: [id: string]; new: []; deleted: [id: string] }>();
const { t, locale } = useI18n();
const snackbar = useSnackbar();
const refresh = useRefreshConversations();

const view = ref<'recent' | 'archived'>('recent');
const archived = computed(() => view.value === 'archived');
const list = useConversations(archived);
const conversations = computed(() => list.data.value?.conversations ?? []);
const views = computed(() => [
  { value: 'recent' as const, label: t('playground.recent') },
  { value: 'archived' as const, label: t('playground.archived') },
]);

const menu = (c: ConversationSummary) => [
  c.archived
    ? { id: 'unarchive', label: t('playground.unarchive'), icon: icons.ArchiveRestore }
    : { id: 'archive', label: t('playground.archive'), icon: icons.Archive },
  { id: 'delete', label: t('playground.delete'), icon: icons.Trash2, danger: true },
];
const titleOf = (c: ConversationSummary) => c.title || t('playground.untitled');
const when = (c: ConversationSummary) => relative(c.updated_at * 1000 - Date.now(), locale.value);

const doomed = ref<ConversationSummary | null>(null);
const confirming = computed({ get: () => doomed.value !== null, set: (open) => !open && (doomed.value = null) });
const deleting = ref(false);

async function act(action: string, c: ConversationSummary) {
  if (action === 'delete') {
    doomed.value = c;
    return;
  }
  try {
    await conversationsApi.update(c.id, { archived: action === 'archive' });
    snackbar.show(action === 'archive' ? t('playground.archivedOne') : t('playground.restoredOne'));
  } catch (error) {
    snackbar.error(error instanceof Error ? error.message : t('common.error'));
  }
  void refresh();
}

async function remove() {
  const c = doomed.value;
  if (!c) return;
  deleting.value = true;
  try {
    await conversationsApi.remove(c.id);
    emit('deleted', c.id);
    snackbar.show(t('playground.deletedOne'));
  } catch (error) {
    snackbar.error(error instanceof Error ? error.message : t('common.error'));
  } finally {
    deleting.value = false;
    doomed.value = null;
    void refresh();
  }
}
</script>

<template>
  <div class="conversation-list">
    <AppButton variant="tonal" :icon="icons.MessageSquarePlus" class="new" @click="emit('new')">{{ t('playground.newChat') }}</AppButton>
    <SegmentedControl v-model="view" :options="views" :label="t('playground.history')" />
    <div v-if="list.isPending.value" class="items"><Skeleton v-for="i in 4" :key="i" height="52px" shape="medium" /></div>
    <EmptyState
      v-else-if="!conversations.length"
      :icon="archived ? icons.Archive : icons.MessagesSquare"
      :title="archived ? t('playground.noArchived') : t('playground.noConversations')"
      :text="archived ? t('playground.noArchivedText') : t('playground.noConversationsText')"
    />
    <ul v-else class="items app-scroll app-scroll-auto" :aria-label="archived ? t('playground.archived') : t('playground.recent')">
      <li v-for="c in conversations" :key="c.id" class="item" :class="{ current: c.id === props.current }">
        <button type="button" class="open state-layer" :aria-current="c.id === props.current ? 'true' : undefined" @click="emit('select', c.id)">
          <span class="title type-body-medium" :class="{ muted: !c.title }" :title="titleOf(c)">{{ titleOf(c) }}</span>
          <span class="meta type-body-small muted">{{ when(c) }}<template v-if="c.model"> · {{ c.model }}</template></span>
        </button>
        <AppMenu :items="menu(c)" @select="act($event, c)">
          <template #default="{ toggle }"><IconButton :icon="icons.MoreVertical" :label="t('common.more')" @click="toggle" /></template>
        </AppMenu>
      </li>
    </ul>
    <ConfirmDialog
      v-model:open="confirming"
      :title="t('playground.deleteTitle')"
      :message="doomed ? t('playground.deleteText', { title: titleOf(doomed) }) : ''"
      :confirm-label="t('playground.delete')"
      :cancel-label="t('common.cancel')"
      :loading="deleting"
      danger
      @confirm="remove"
    />
  </div>
</template>

<style scoped>
.conversation-list { display: flex; flex-direction: column; gap: var(--app-space-3); min-width: 0; min-height: 0; }
.new { align-self: flex-start; }
/* Scrolls on its own where the list is given a height (beside the conversation); in a sheet the sheet scrolls. */
.items { display: flex; flex-direction: column; gap: 2px; flex: 1 1 auto; min-height: 0; margin: 0; padding: 0; list-style: none; }
.item { display: flex; align-items: center; gap: var(--app-space-1); min-width: 0; border-radius: var(--md-sys-shape-corner-medium); }
.item.current { background: var(--md-sys-color-secondary-container); color: var(--md-sys-color-on-secondary-container); }
.open {
  display: flex; flex: 1; flex-direction: column; align-items: flex-start; gap: 2px; min-width: 0; padding: var(--app-space-2) var(--app-space-1) var(--app-space-2) var(--app-space-3);
  border: 0; border-radius: inherit; background: transparent; color: inherit; font: inherit; text-align: start; cursor: pointer;
}
.title, .meta { max-width: 100%; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.item.current .muted { color: inherit; opacity: 0.8; }
</style>
