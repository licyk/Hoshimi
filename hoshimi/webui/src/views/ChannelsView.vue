<script setup lang="ts">
import { computed, ref, watch } from 'vue';
import { RouterLink, useRouter } from 'vue-router';
import { useBalances, useChannels, useClaimCredits, useRefreshNow } from '@/api/queries/channels';
import type { ChannelView } from '@/api/types';
import ChannelDetail from '@/components/ChannelDetail.vue';
import { compact } from '@/format';
import { useI18n } from '@/i18n';
import {
  AppButton,
  AxisPanes,
  Badge,
  EmptyState,
  IconButton,
  ListDetail,
  Meter,
  SearchField,
  SegmentedControl,
  Skeleton,
  TRANSITIONS,
  Toolbar,
  icons,
  keepLeavingInPlace,
  staggerStyle,
  useSnackbar,
} from '@/ui';

/**
 * Channels as a list and a detail: the stations on the left, searchable and filtered by what needs
 * doing, each row saying its state at a glance; the chosen one's page on the right (or alone, with
 * a way back, when the pane is narrow). The chosen channel is the route's ``id``.
 */
const props = defineProps<{ id?: string }>();
const { t } = useI18n();
const router = useRouter();
const snackbar = useSnackbar();
const report = useChannels();
const balances = useBalances();
const claim = useClaimCredits();
const refreshNow = useRefreshNow();

type Filter = 'all' | 'inUse' | 'attention' | 'setup' | 'disabled';
const FILTERS: Filter[] = ['all', 'inUse', 'attention', 'setup', 'disabled'];
const filter = ref<Filter>('all');
const search = ref('');
const channels = computed(() => report.data.value?.channels ?? []);
const inFilter = (c: ChannelView, f: Filter) =>
  f === 'all' || (f === 'inUse' && (c.status === 'ok' || c.status === 'attention')) || (f === 'attention' && c.status === 'attention') || (f === 'disabled' && c.status === 'disabled') || (f === 'setup' && c.status === 'empty');
const counts = computed(() => Object.fromEntries(FILTERS.map((f) => [f, channels.value.filter((c) => inFilter(c, f)).length])) as Record<Filter, number>);
// Saving a channel (switching it on, adding its first account) changes where the server lists it;
// a row must not jump from under the pointer, so rows keep the places they were first shown in and
// newly listed channels join at the end. Memory only: a reload takes the server's order afresh.
const order = ref<string[]>([]);
watch(
  channels,
  (list) => {
    const known = new Set(order.value);
    const added = list.filter((c) => !known.has(c.id)).map((c) => c.id);
    if (added.length) order.value = [...order.value, ...added];
  },
  { immediate: true },
);
const shown = computed(() => {
  const q = search.value.trim().toLowerCase();
  const byId = new Map(channels.value.map((c) => [c.id, c]));
  return order.value.flatMap((id) => {
    const c = byId.get(id);
    return c && inFilter(c, filter.value) && (!q || `${c.id} ${c.display_name}`.toLowerCase().includes(q)) ? [c] : [];
  });
});
const shownIds = computed(() => shown.value.map((c) => c.id));
const selected = computed(() => channels.value.find((c) => c.id === props.id) ?? null);
const filterOptions = computed(() => FILTERS.map((f) => ({ value: f, label: t(`channels.filters.${f}`), count: counts.value[f] })));
// Another filter is another list: it slides in whole (AxisPanes) from the side its option sits on,
// starting at its top. Within one filter (a search, a status change) rows move on their own.
const listTop = ref<HTMLElement | null>(null);
watch(filter, () => listTop.value?.closest('.app-scroll')?.scrollTo({ top: 0 }));
// A channel opened by its address shows in the list even when the filter would hide it.
watch(
  selected,
  (c) => {
    if (c && !inFilter(c, filter.value)) filter.value = 'all';
  },
  { immediate: true },
);
// A channel that went away (forgotten in another tab, renamed) leaves its page for the list.
watch([selected, () => report.data.value], ([value, data]) => {
  if (props.id && data && !value) void router.replace('/channels');
});

const STATUS_TONE: Record<string, 'error' | 'warning' | 'primary' | 'neutral'> = { attention: 'warning', ok: 'primary', empty: 'neutral', disabled: 'neutral' };
const line = (c: ChannelView) => {
  const parts = [c.accounts.length ? t('channels.accountCount', { n: c.accounts.length }) : c.keyless ? t('channels.keyless') : t('channels.noAccountsShort')];
  const tokens = c.usage_today.input + c.usage_today.output;
  if (tokens) parts.push(t('channels.tokensTodayN', { n: compact(tokens) }));
  return parts.join(' · ');
};

const checkin = computed(() => report.data.value?.checkin);
const checkinLine = computed(() =>
  checkin.value?.last_date ? t('channels.checkedAt', { when: new Date(checkin.value.last_at * 1000).toLocaleTimeString() }) : t('channels.notChecked'),
);
function act(mutation: { mutate: (v: never, opts: Record<string, unknown>) => void }, done: string) {
  mutation.mutate(null as never, { onSuccess: () => snackbar.show(done), onError: (e: Error) => snackbar.error(e.message) });
}
</script>

<template>
  <div class="channels">
    <div class="head">
      <Toolbar>
        <SearchField v-model="search" :label="t('channels.search')" :placeholder="t('channels.search')" class="search" />
        <template #end>
          <span class="type-body-small muted checkin" :title="checkin?.last_result ?? ''">{{ checkinLine }}</span>
          <AppButton variant="tonal" :icon="icons.CalendarCheck" :loading="claim.isPending.value" @click="act(claim, t('channels.claimStarted'))">{{ t('channels.claimAll') }}</AppButton>
          <IconButton :icon="icons.RotateCcw" :label="t('channels.refreshAll')" :spin="refreshNow.isPending.value" @click="act(refreshNow, t('channels.refreshStarted'))" />
        </template>
      </Toolbar>
      <SegmentedControl v-model="filter" class="filters" :options="filterOptions" :label="t('channels.filter')" />
    </div>

    <div v-if="report.isPending.value" class="skeletons"><Skeleton v-for="i in 5" :key="i" height="64px" shape="medium" /></div>
    <EmptyState v-else-if="!channels.length" :icon="icons.Waves" :title="t('channels.empty')" :text="t('channels.emptyText')" />
    <ListDetail v-else class="layout" :detail="!!selected" :detail-key="selected?.id ?? null" :order="shownIds">
      <template #list>
        <span ref="listTop" class="list-top" aria-hidden="true" />
        <AxisPanes :value="filter" :order="FILTERS">
          <div :key="filter" class="list-page">
            <EmptyState v-if="!shown.length" :icon="icons.Search" :title="t('channels.noMatch')" />
            <TransitionGroup v-else :name="TRANSITIONS.list" tag="ul" class="list" role="list" appear v-bind="keepLeavingInPlace">
              <li v-for="(c, i) in shown" :key="c.id" :style="staggerStyle(i)">
                <RouterLink v-slot="{ href, navigate }" :to="`/channels/${c.id}`" custom>
                  <a :href="href" class="row state-layer" :class="{ selected: c.id === id }" :aria-current="c.id === id ? 'page' : undefined" @click="navigate">
                    <span class="status-dot" :class="c.status" aria-hidden="true" />
                    <span class="row-text">
                      <span class="type-title-small name">{{ c.display_name }}</span>
                      <span class="type-body-small muted sub">{{ line(c) }}</span>
                      <Meter v-if="c.quota?.percent !== null && c.quota?.percent !== undefined" class="quota" :value="c.quota.percent / 100" :tone="c.quota.percent <= 10 ? 'error' : c.quota.percent <= 25 ? 'warning' : 'primary'" :label="t('channels.quota', { pct: c.quota.percent })" :height="4" />
                    </span>
                    <Badge v-if="c.status !== 'ok'" :tone="STATUS_TONE[c.status]" :value="t(`channels.statuses.${c.status}`)" />
                  </a>
                </RouterLink>
              </li>
            </TransitionGroup>
          </div>
        </AxisPanes>
      </template>
      <template #detail="{ wide }">
        <ChannelDetail v-if="selected" :key="selected.id" :channel="selected" :balances="balances.data.value" :back="!wide" />
      </template>
      <template #placeholder>
        <EmptyState :icon="icons.Fingerprint" :title="t('channels.choose')" :text="t('channels.chooseText')" />
      </template>
    </ListDetail>
  </div>
</template>

<style scoped>
/* With room for both panes the page is the window's height and each pane scrolls on its own;
   with one pane the page scrolls as a whole. Decided by the panes' own width, as ListDetail does. */
.channels { display: flex; flex-direction: column; gap: var(--app-space-3); min-height: 0; padding: var(--app-space-4) var(--app-space-6) var(--app-space-4); }
.search { flex: 1 1 240px; max-width: 480px; }
.checkin { white-space: nowrap; }
.filters { align-self: flex-start; }
.list-top { display: block; height: 0; }
.skeletons { display: flex; flex-direction: column; gap: var(--app-space-2); }
.layout { flex: 1; min-height: 0; }
.list { position: relative; display: flex; flex-direction: column; gap: var(--app-space-1); margin: 0; padding: 0 0 var(--app-space-4); list-style: none; }
.row {
  display: flex; align-items: center; gap: var(--app-space-3); min-height: 64px; padding: var(--app-space-2) var(--app-space-3) var(--app-space-2) var(--app-space-4);
  border-radius: var(--md-sys-shape-corner-large); background: var(--md-sys-color-surface); color: var(--md-sys-color-on-surface); text-decoration: none;
  transition: background-color var(--md-sys-motion-duration-short4) var(--md-sys-motion-easing-standard);
}
.row.selected { background: var(--md-sys-color-secondary-container); color: var(--md-sys-color-on-secondary-container); }
.row:focus-visible { outline-offset: -2px; }
.status-dot { flex: none; width: 8px; height: 8px; border-radius: 50%; background: var(--md-sys-color-primary); }
.status-dot.attention { background: var(--md-sys-color-tertiary); }
.status-dot.empty, .status-dot.disabled { background: var(--md-sys-color-outline-variant); }
.row-text { display: flex; flex-direction: column; gap: 2px; flex: 1; min-width: 0; }
.name, .sub { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.selected .muted { color: inherit; opacity: 0.8; }
.quota { margin-top: var(--app-space-1); max-width: 240px; }
.channels:has(.layout.wide) { height: 100%; }
.head { display: flex; flex-direction: column; gap: var(--app-space-3); }
/* A channel shown alone (a narrow pane) is its own page: the list's search and filters wait behind its back button. */
.channels:has(.layout.detail:not(.wide)) .head { display: none; }
@container app-content (max-width: 599px) {
  .channels { padding: var(--app-space-3); }
  .checkin { display: none; }
}
</style>
