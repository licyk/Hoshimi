<script setup lang="ts">
import { computed, ref, watch } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import { RANGES, useCalls, useResetStats, useUsageReport, type CallFilters, type UsageRange } from '@/api/queries/usage';
import type { CallRecord, UsageBreakdownRow, UsageTotals } from '@/api/types';
import CallDetailSheet from '@/components/CallDetailSheet.vue';
import LiveCallCard from '@/components/LiveCallCard.vue';
import { bucketLabel, change, compact, duration, grouped, percent, ratio, when } from '@/format';
import { useI18n } from '@/i18n';
import { useLiveStore } from '@/stores/live';
import {
  AppMenu,
  AxisPanes,
  Badge,
  Chip,
  ConfirmDialog,
  DataTable,
  EmptyState,
  IconButton,
  LineChart,
  LiveDot,
  Meter,
  PageSection,
  SegmentedButton,
  Skeleton,
  StackedBarChart,
  StatTile,
  Tabs,
  Toolbar,
  TRANSITIONS,
  icons,
  keepLeavingInPlace,
  staggerStyle,
  useSnackbar,
  type Column,
  type SortState,
  type StackSeries,
} from '@/ui';

const { t } = useI18n();
const route = useRoute();
const router = useRouter();
const live = useLiveStore();
const snackbar = useSnackbar();

// The range lives in the address (?range=7d), so a reload or a shared link shows the same slice.
const range = computed<UsageRange>({
  get: () => (RANGES.includes(route.query.range as UsageRange) ? (route.query.range as UsageRange) : '7d'),
  set: (value) => void router.replace({ query: { ...route.query, range: value } }),
});
const report = useUsageReport(range);
const r = computed(() => report.data.value);
const busy = computed(() => report.isFetching.value && !report.isPending.value);
const rangeOptions = computed(() => RANGES.map((v) => ({ value: v, label: t(`usage.ranges.${v}`) })));
const previousLabel = computed(() => t('usage.vsPrevious', { range: t(`usage.ranges.${range.value}`) }));

// -- headline tiles ----------------------------------------------------------- #
const total = (u: UsageTotals | undefined) => (u ? u.input + u.output : 0);
const uncached = (u: Pick<UsageTotals, 'input' | 'cache_read' | 'cache_write'>) => Math.max(0, u.input - u.cache_read - u.cache_write);
const series = computed(() => r.value?.series ?? []);
const trend = (pick: (p: (typeof series.value)[number]) => number) => series.value.map(pick);
const tiles = computed(() => {
  const now = r.value?.totals;
  const before = r.value?.previous;
  if (!now || !before) return [];
  const hit = ratio(now.cache_read, now.input);
  const hitBefore = ratio(before.cache_read, before.input);
  const failRate = ratio(now.failed, now.calls);
  const lat = r.value!.latency;
  return [
    { key: 'total', label: t('usage.tiles.total'), value: total(now), format: compact, delta: change(total(now), total(before)), icon: icons.Coins, trend: trend((p) => p.input + p.output), detail: t('usage.tiles.totalDetail', { calls: grouped(now.calls) }) },
    { key: 'input', label: t('usage.tiles.input'), value: now.input, format: compact, delta: change(now.input, before.input), icon: icons.ArrowDown, trend: trend((p) => p.input), detail: t('usage.tiles.inputDetail', { uncached: compact(uncached(now)), write: compact(now.cache_write) }) },
    { key: 'output', label: t('usage.tiles.output'), value: now.output, format: compact, delta: change(now.output, before.output), icon: icons.ArrowUp, trend: trend((p) => p.output), detail: t('usage.tiles.outputDetail', { reasoning: compact(now.reasoning) }) },
    { key: 'cache', label: t('usage.tiles.cache'), value: hit === null ? null : hit * 100, format: (n: number) => `${n.toFixed(1)}%`, delta: hit !== null && hitBefore !== null && hitBefore > 0 ? (hit - hitBefore) / hitBefore : null, icon: icons.Database, trend: trend((p) => (p.input ? p.cache_read / p.input : 0)), detail: t('usage.tiles.cacheDetail', { read: compact(now.cache_read) }) },
    { key: 'calls', label: t('usage.tiles.calls'), value: now.calls, format: compact, delta: change(now.calls, before.calls), icon: icons.Activity, trend: trend((p) => p.calls), detail: t('usage.tiles.callsDetail', { failed: grouped(now.failed), rate: percent(failRate) }) },
    { key: 'latency', label: t('usage.tiles.latency'), value: lat.ttft_p50_ms, format: (n: number) => duration(n), delta: null, upIsGood: false, icon: icons.Timer, trend: trend((p) => p.ttft_avg_ms ?? 0), detail: t('usage.tiles.latencyDetail', { p95: duration(lat.ttft_p95_ms), tps: lat.tps_p50 ?? '—' }) },
  ];
});

// -- the token chart ---------------------------------------------------------- #
// Fixed slots: a kind keeps its colour whatever the range or the data (never re-assigned by rank).
const stackSeries = computed<StackSeries[]>(() => [
  { key: 'uncached', label: t('usage.series.uncached'), slot: 1 },
  { key: 'cacheRead', label: t('usage.series.cacheRead'), slot: 2 },
  { key: 'cacheWrite', label: t('usage.series.cacheWrite'), slot: 3 },
  { key: 'answer', label: t('usage.series.answer'), slot: 4 },
  { key: 'reasoning', label: t('usage.series.reasoning'), slot: 5 },
]);
const granularity = computed(() => (r.value?.granularity ?? 'day') as 'hour' | 'day');
const stackPoints = computed(() =>
  series.value.map((p) => ({
    key: p.start,
    label: bucketLabel(p.start, granularity.value),
    title: granularity.value === 'hour' ? when(p.start) : p.start,
    // Output counts the reasoning inside it, so the answer is what is left of it.
    values: [uncached(p), p.cache_read, p.cache_write, Math.max(0, p.output - p.reasoning), p.reasoning],
  })),
);
const xLabels = computed(() => series.value.map((p) => bucketLabel(p.start, granularity.value)));
const ttftSeries = computed(() => [{ label: t('usage.ttftAvg'), slot: 1, points: series.value.map((p, i) => [i, p.ttft_avg_ms ?? NaN] as [number, number]) }]);
const tpsSeries = computed(() => [{ label: t('usage.tpsAvg'), slot: 1, points: series.value.map((p, i) => [i, p.tps ?? NaN] as [number, number]) }]);
const hasSpeed = computed(() => series.value.some((p) => p.ttft_avg_ms !== null || p.tps !== null));

// -- breakdown ---------------------------------------------------------------- #
type Group = 'model' | 'provider' | 'account' | 'origin';
const GROUPS = ['model', 'provider', 'account', 'origin'] as const;
const group = ref<Group>('model');
const groupTabs = computed(() => GROUPS.map((g) => ({ value: g, label: t(`usage.groups.${g}`) })));
const breakdown = computed<UsageBreakdownRow[]>(() => (r.value ? r.value[`by_${group.value}`] : []));
const breakdownSort = ref<SortState | null>({ key: 'tokens', dir: 'desc' });
const breakdownColumns = computed<Column[]>(() => [
  { key: 'label', label: t(`usage.groups.${group.value}`), sortable: true },
  { key: 'calls', label: t('usage.calls'), align: 'end', width: '72px', sortable: true },
  { key: 'tokens', label: t('usage.tokens'), align: 'end', width: '88px', sortable: true },
  { key: 'cache', label: t('usage.cacheHit'), width: '128px', sortable: true, minWidth: 720 },
  { key: 'output', label: t('usage.outputShort'), align: 'end', width: '80px', sortable: true, minWidth: 560 },
  { key: 'ttft_p50_ms', label: t('usage.ttftP50'), align: 'end', width: '80px', sortable: true, minWidth: 820 },
  { key: 'ttft_p95_ms', label: t('usage.ttftP95'), align: 'end', width: '80px', sortable: true, minWidth: 960 },
  { key: 'tps', label: t('usage.tps'), align: 'end', width: '64px', sortable: true, minWidth: 640 },
  { key: 'failed', label: t('usage.failed'), align: 'end', width: '72px', sortable: true, minWidth: 480 },
]);
const breakdownValue = (row: UsageBreakdownRow, key: string): unknown => {
  if (key === 'tokens') return row.input + row.output;
  if (key === 'cache') return ratio(row.cache_read, row.input);
  return (row as unknown as Record<string, unknown>)[key];
};
/** Choosing a row narrows the call list below to it. */
function focusRow(row: UsageBreakdownRow) {
  const filter: CallFilters = group.value === 'account' ? { provider: row.provider, account: row.account } : { [group.value]: row.key };
  filters.value = { ...filters.value, model: undefined, provider: undefined, account: undefined, origin: undefined, ...filter };
  focusLabel.value = row.label || row.key;
  callsSection.value?.scrollIntoView({ behavior: 'smooth', block: 'start' });
}

// -- calls -------------------------------------------------------------------- #
const filters = ref<CallFilters>({});
const focusLabel = ref('');
const failedOnly = computed({ get: () => filters.value.ok === false, set: (v: boolean) => (filters.value = { ...filters.value, ok: v ? false : undefined }) });
const narrowed = computed(() => !!(filters.value.model || filters.value.provider || filters.value.origin));
function clearFocus() {
  filters.value = { ok: filters.value.ok };
  focusLabel.value = '';
}
const calls = useCalls(filters);
const callRows = computed<CallRecord[]>(() => calls.data.value?.pages.flatMap((p) => p.calls) ?? []);
const callColumns = computed<Column[]>(() => [
  { key: 'at', label: t('usage.at'), width: '96px' },
  { key: 'model', label: t('usage.model') },
  { key: 'route', label: t('usage.route'), width: 'minmax(0, 0.8fr)', minWidth: 760 },
  { key: 'tokens', label: t('usage.tokens'), align: 'end', width: '80px' },
  { key: 'cache_read', label: t('usage.cachedShort'), align: 'end', width: '72px', minWidth: 900 },
  { key: 'ttft_ms', label: t('usage.ttft'), align: 'end', width: '80px', minWidth: 560 },
  { key: 'elapsed_ms', label: t('usage.elapsed'), align: 'end', width: '80px', minWidth: 640 },
  { key: 'tps', label: t('usage.tps'), align: 'end', width: '56px', minWidth: 1040 },
  { key: 'status', label: t('usage.status'), width: '84px' },
]);
const callsSection = ref<HTMLElement | null>(null);
const chosen = ref<CallRecord | null>(null);
const detailOpen = ref(false);
function openCall(row: CallRecord) {
  chosen.value = row;
  detailOpen.value = true;
}
const time = (iso: string) => {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toDateString() === new Date().toDateString() ? d.toLocaleTimeString() : d.toLocaleDateString();
};

// -- reset -------------------------------------------------------------------- #
const reset = useResetStats();
const confirmReset = ref(false);
const menu = computed(() => [{ id: 'reset', label: t('usage.reset'), icon: icons.Trash2, danger: true }]);
function doReset() {
  reset.mutate(undefined, {
    onSuccess: () => {
      confirmReset.value = false;
      snackbar.show(t('usage.resetDone'));
    },
    onError: (e) => snackbar.error((e as Error).message),
  });
}

const empty = computed(() => !!r.value && !r.value.requests && !live.active.length);
watch(range, () => (chosen.value = null));
</script>

<template>
  <div class="usage">
    <Toolbar>
      <SegmentedButton v-model="range" :options="rangeOptions" />
      <template #end>
        <LiveDot :state="live.state" :label="t(`live.states.${live.state}`)" />
        <IconButton :icon="icons.RefreshCw" :label="t('common.refresh')" :spin="report.isFetching.value" @click="report.refetch(), calls.refetch()" />
        <AppMenu :items="menu" @select="confirmReset = true">
          <template #default="{ toggle }">
            <IconButton :icon="icons.MoreVertical" :label="t('common.more')" @click="toggle" />
          </template>
        </AppMenu>
      </template>
    </Toolbar>

    <div v-if="report.isPending.value" class="tiles"><Skeleton v-for="i in 6" :key="i" height="136px" shape="medium" /></div>
    <EmptyState v-else-if="empty" :icon="icons.BarChart3" :title="t('usage.empty')" :text="t('usage.emptyText')" />
    <template v-else-if="r">
      <div class="tiles" :class="{ busy }">
        <StatTile
          v-for="tile in tiles"
          :key="tile.key"
          :label="tile.label"
          :value="tile.value"
          :format="tile.format"
          :delta="tile.delta"
          :up-is-good="tile.upIsGood ?? true"
          :delta-label="previousLabel"
          :detail="tile.detail"
          :trend="tile.trend"
          :icon="tile.icon"
        />
      </div>

      <Transition :name="TRANSITIONS.collapse">
        <PageSection v-if="live.active.length" :title="t('live.running', { n: live.active.length })" :icon="icons.Radio">
          <TransitionGroup :name="TRANSITIONS.list" tag="div" class="now" v-bind="keepLeavingInPlace">
            <LiveCallCard v-for="(c, i) in live.active" :key="c.id" :call="c" :style="staggerStyle(i)" />
          </TransitionGroup>
        </PageSection>
      </Transition>

      <PageSection :title="t('usage.overTime')" :subtitle="t(`usage.granularity.${granularity}`)" :icon="icons.BarChart3">
        <StackedBarChart :series="stackSeries" :points="stackPoints" :label="t('usage.overTime')" :format="compact" :busy="busy" :total-label="t('usage.total')" />
      </PageSection>

      <PageSection v-if="hasSpeed" :title="t('usage.speed')" :subtitle="t('usage.speedHint')" :icon="icons.Gauge">
        <div class="speed">
          <div class="speed-chart">
            <h3 class="type-title-small">{{ t('usage.ttftAvg') }}</h3>
            <LineChart :series="ttftSeries" :x-labels="xLabels" :format="(n) => duration(n)" :height="180" />
          </div>
          <div class="speed-chart">
            <h3 class="type-title-small">{{ t('usage.tpsAvg') }}</h3>
            <LineChart :series="tpsSeries" :x-labels="xLabels" :format="(n) => String(Math.round(n))" :height="180" />
          </div>
        </div>
      </PageSection>

      <PageSection :title="t('usage.breakdown')" :icon="icons.Layers">
        <Tabs v-model="group" :tabs="groupTabs" />
        <AxisPanes :value="group" :order="GROUPS">
          <DataTable
            :key="group"
            v-model:sort="breakdownSort"
            :columns="breakdownColumns"
            :rows="breakdown"
            :row-key="(row) => row.key"
            :value="breakdownValue"
            :label="t('usage.breakdown')"
            :busy="busy"
            :empty-text="t('usage.noneInRange')"
            clickable
            max-height="min(var(--app-height-list), 480px)"
            @select="focusRow"
          >
            <template #cell-label="{ row }">
              <span class="name-cell">
                <span class="text" :title="row.key">{{ row.label || row.key }}</span>
                <span v-if="row.label && row.label !== row.key" class="type-body-small muted text mono">{{ row.key }}</span>
              </span>
            </template>
            <template #cell-tokens="{ row }">{{ compact(row.input + row.output) }}</template>
            <template #cell-output="{ row }">{{ compact(row.output) }}</template>
            <template #cell-cache="{ row }">
              <span class="cache-cell">
                <Meter
                  :label="t('usage.cacheHit')"
                  :segments="[
                    { label: t('usage.series.uncached'), value: Math.max(0, row.input - row.cache_read - row.cache_write), slot: 1 },
                    { label: t('usage.series.cacheRead'), value: row.cache_read, slot: 2 },
                    { label: t('usage.series.cacheWrite'), value: row.cache_write, slot: 3 },
                  ]"
                />
                <span class="type-body-small num">{{ percent(ratio(row.cache_read, row.input), 0) }}</span>
              </span>
            </template>
            <template #cell-ttft_p50_ms="{ row }">{{ duration(row.ttft_p50_ms) }}</template>
            <template #cell-ttft_p95_ms="{ row }">{{ duration(row.ttft_p95_ms) }}</template>
            <template #cell-tps="{ row }">{{ row.tps ?? '—' }}</template>
            <template #cell-failed="{ row }"><Badge v-if="row.failed" tone="error" :value="row.failed" /><span v-else class="muted">0</span></template>
          </DataTable>
        </AxisPanes>
      </PageSection>

      <div ref="callsSection" class="anchor">
        <PageSection :title="t('usage.calls')" :subtitle="t('usage.callsHint')" :icon="icons.ListChecks">
          <template #actions>
            <Chip :label="t('usage.failedOnly')" :icon="icons.CircleX" :selected="failedOnly" @click="failedOnly = !failedOnly" />
            <Chip v-if="narrowed" :label="focusLabel" selected removable @remove="clearFocus" @click="clearFocus" />
          </template>
          <DataTable
            :columns="callColumns"
            :rows="callRows"
            :row-key="(row) => row.id"
            :label="t('usage.calls')"
            :busy="calls.isFetching.value && !calls.isFetchingNextPage.value"
            :empty-text="t('usage.noCalls')"
            clickable
            :selected="detailOpen ? (chosen?.id ?? null) : null"
            @select="openCall"
            @end="calls.hasNextPage.value && !calls.isFetchingNextPage.value && calls.fetchNextPage()"
          >
            <template #cell-at="{ row }"><span class="text num" :title="when(row.at)">{{ time(row.at) }}</span></template>
            <template #cell-model="{ row }">
              <span class="name-cell">
                <span class="text" :title="row.model">{{ row.name || row.model }}</span>
                <span class="type-body-small muted text">{{ t(`usage.origins.${row.origin}`) }}<template v-if="row.effort"> · {{ row.effort }}</template></span>
              </span>
            </template>
            <template #cell-route="{ row }"><span class="text muted" :title="[row.provider, row.account].filter(Boolean).join(' / ')">{{ [row.provider, row.account].filter(Boolean).join(' / ') || '—' }}</span></template>
            <template #cell-tokens="{ row }">{{ compact(row.input + row.output) }}</template>
            <template #cell-cache_read="{ row }">{{ row.cache_read ? compact(row.cache_read) : '—' }}</template>
            <template #cell-ttft_ms="{ row }">{{ duration(row.ttft_ms) }}</template>
            <template #cell-elapsed_ms="{ row }">{{ duration(row.elapsed_ms) }}</template>
            <template #cell-tps="{ row }">{{ row.tps ?? '—' }}</template>
            <template #cell-status="{ row }"><Badge :tone="row.ok ? 'primary' : 'error'" :value="row.ok ? t('usage.served') : t('usage.failedOne')" /></template>
            <template #footer>
              <p v-if="calls.isFetchingNextPage.value" class="type-body-small muted more">{{ t('common.loading') }}</p>
            </template>
          </DataTable>
        </PageSection>
      </div>
    </template>

    <CallDetailSheet v-model:open="detailOpen" :call="chosen" />
    <ConfirmDialog
      v-model:open="confirmReset"
      :title="t('usage.resetTitle')"
      :message="t('usage.resetMessage')"
      :confirm-label="t('usage.reset')"
      :cancel-label="t('common.cancel')"
      danger
      :loading="reset.isPending.value"
      @confirm="doReset"
    />
  </div>
</template>

<style scoped>
.usage { display: flex; flex-direction: column; gap: var(--app-space-4); padding: var(--app-space-4) var(--app-space-6) var(--app-space-8); min-width: 0; }
.tiles { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: var(--app-space-3); transition: opacity var(--md-sys-motion-duration-short4) var(--md-sys-motion-easing-standard); }
.tiles.busy { opacity: 0.7; }
.now { position: relative; display: grid; grid-template-columns: repeat(auto-fill, minmax(300px, 1fr)); gap: var(--app-space-3); }
.speed { display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: var(--app-space-4); }
.speed-chart { display: flex; flex-direction: column; gap: var(--app-space-2); min-width: 0; }
.speed-chart h3 { margin: 0; color: var(--md-sys-color-on-surface-variant); }
.name-cell { display: flex; flex-direction: column; min-width: 0; }
.text { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.cache-cell { display: grid; grid-template-columns: minmax(0, 1fr) 36px; align-items: center; gap: var(--app-space-2); width: 100%; }
.cache-cell .num { text-align: end; }
.anchor { scroll-margin-top: var(--app-space-4); }
.more { margin: 0; padding: var(--app-space-2); text-align: center; }
/* Six tiles: one row where they fit, else two rows of three, else three rows of two. */
@container app-content (min-width: 1240px) {
  .tiles { grid-template-columns: repeat(6, minmax(0, 1fr)); }
}
@container app-content (max-width: 599px) {
  .usage { padding: var(--app-space-3); }
  .tiles { grid-template-columns: repeat(2, minmax(0, 1fr)); gap: var(--app-space-2); }
}
</style>
