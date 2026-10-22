<script setup lang="ts">
import { useQueryClient } from '@tanstack/vue-query';
import { computed, onBeforeUnmount, ref, watch } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import { RANGES, prefetchUsage, useCalls, useResetStats, useUsageDays, useUsageReport, type CallFilters, type UsageRange, type UsageSlice } from '@/api/queries/usage';
import type { CallRecord, UsageBreakdownRow, UsageTotals } from '@/api/types';
import CallDetailSheet from '@/components/CallDetailSheet.vue';
import LiveCallCard from '@/components/LiveCallCard.vue';
import { addDays, bucketLabel, change, compact, duration, grouped, localDay, longDay, percent, ratio, when } from '@/format';
import { useI18n } from '@/i18n';
import { useLiveStore } from '@/stores/live';
import {
  AppButton,
  AppMenu,
  AxisPanes,
  Badge,
  Chip,
  Collapse,
  ConfirmDialog,
  DataTable,
  DatePicker,
  EmptyState,
  FadeSwap,
  IconButton,
  LineChart,
  LiveDot,
  Meter,
  PageSection,
  ProgressBar,
  SearchField,
  SegmentedControl,
  Skeleton,
  StackedBarChart,
  StatTile,
  Tabs,
  Toolbar,
  TRANSITIONS,
  TweenNumber,
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

// The slice lives in the address (?range=7d, ?range=day&date=2026-10-20), so a reload or a shared
// link shows the same one. Today's day has no date: it stays today across midnight.
const today = ref(localDay());
const midnight = setInterval(() => (today.value = localDay()), 60_000);
onBeforeUnmount(() => clearInterval(midnight));
const range = computed<UsageRange>({
  get: () => (RANGES.includes(route.query.range as UsageRange) ? (route.query.range as UsageRange) : '7d'),
  set: (value) => void router.replace({ query: { ...route.query, range: value, date: value === 'day' ? route.query.date : undefined } }),
});
const date = computed<string | null>({
  get: () => {
    const q = route.query.date;
    return range.value === 'day' && typeof q === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(q) && q < today.value ? q : null;
  },
  set: (value) => void router.replace({ query: { ...route.query, range: 'day', date: value && value < today.value ? value : undefined } }),
});
const day = computed(() => date.value ?? today.value);
// The calendar always has a day (today's has no date in the address).
const pickedDay = computed<string>({ get: () => day.value, set: (value) => (date.value = value) });
const slice = computed<UsageSlice>(() => ({ range: range.value, date: date.value }));
const report = useUsageReport(slice);
const r = computed(() => report.data.value);
// The page shows the last answer until the next lands, then moves to it in one step; meanwhile a
// thin bar under the toolbar says it is on its way.
const loading = computed(() => report.isFetching.value && report.isPlaceholderData.value);
const qc = useQueryClient();
const stopPrefetch = watch(
  () => !!r.value,
  (ready) => {
    if (!ready) return;
    prefetchUsage(qc);
    queueMicrotask(() => stopPrefetch());
  },
  { immediate: true },
);
const rangeOptions = computed(() => RANGES.map((v) => ({ value: v, label: t(`usage.ranges.${v}`) })));
// What the numbers on screen are (the report that landed), not what was last clicked.
const shownRange = computed(() => (r.value?.range ?? range.value) as UsageRange);
const shownDay = computed(() => r.value?.date ?? null);
const shownToday = computed(() => shownRange.value === 'day' && shownDay.value === today.value);
const previousLabel = computed(() => {
  if (shownRange.value !== 'day') return t('usage.vsPrevious', { range: t(`usage.ranges.${shownRange.value}`) });
  return shownToday.value ? t('usage.vsYesterdaySoFar') : t('usage.vsDayBefore');
});

// -- the day ------------------------------------------------------------------ #
const days = useUsageDays();
const firstDay = computed(() => days.data.value?.first ?? null);
const marks = computed(() => {
  const list = days.data.value?.days ?? [];
  const most = Math.max(1, ...list.map((d) => d.tokens));
  return Object.fromEntries(list.map((d) => [d.date, d.tokens / most]));
});
const dayTitle = computed(() => (day.value === today.value ? t('usage.today') : day.value === addDays(today.value, -1) ? t('usage.yesterday') : longDay(day.value)));
function stepDay(n: number) {
  const next = addDays(day.value, n);
  if (next > today.value || (firstDay.value && next < firstDay.value)) return;
  date.value = next;
}
/** A day's column in a longer range opens that day, hour by hour. */
function openDay(key: string) {
  date.value = key.slice(0, 10);
}

// -- headline tiles ----------------------------------------------------------- #
const total = (u: UsageTotals | undefined) => (u ? u.input + u.output : 0);
const uncached = (u: Pick<UsageTotals, 'input' | 'cache_read' | 'cache_write'>) => Math.max(0, u.input - u.cache_read - u.cache_write);
const series = computed(() => r.value?.series ?? []);
const serverNow = computed(() => (r.value?.server_time ? Date.parse(r.value.server_time) : Date.now()));

// A trend ends now: the hours of today still to come are not a fall to zero.
const trend = (pick: (p: (typeof series.value)[number]) => number) => series.value.filter((p) => r.value?.granularity !== 'hour' || Date.parse(p.start) <= serverNow.value).map(pick);
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
    title: granularity.value === 'hour' ? when(p.start) : longDay(p.start.slice(0, 10)),
    // Output counts the reasoning inside it, so the answer is what is left of it.
    values: [uncached(p), p.cache_read, p.cache_write, Math.max(0, p.output - p.reasoning), p.reasoning],
    // The rest of today has not happened yet.
    muted: granularity.value === 'hour' && Date.parse(p.start) > serverNow.value,
  })),
);
// Only a day reads hours; the longer ranges count whole days by design.
const hourlyMissing = computed(() => r.value?.range === 'day' && r.value.hourly === false);
// The charts slide when what they count changes (hours to days, one day to another) and reshape in
// place when it does not (7 to 30 days, the last 24 hours to today). Later is further right.
const chartKey = computed(() => {
  const rr = r.value;
  if (!rr) return 'none';
  if (rr.granularity === 'day') return 'days';
  return rr.range === 'day' && rr.date && rr.date !== today.value ? `day:${rr.date}` : 'hours';
});
const rank = (key: string) => (key === 'days' ? '~' : key === 'hours' ? today.value : key.slice(4));
const seenKeys = ref<string[]>([]);
watch(chartKey, (key) => seenKeys.value.includes(key) || (seenKeys.value = [...seenKeys.value, key]), { immediate: true, flush: 'sync' });
const chartOrder = computed(() => [...seenKeys.value].sort((a, b) => rank(a).localeCompare(rank(b))));
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
// The history follows the slice on screen: a past day is closed at both ends; a range that runs
// to now is open at its end, so calls that finish join the top of the list instead of renewing it.
const filters = ref<CallFilters>({});
const callSearch = ref('');
let searchTimer: ReturnType<typeof setTimeout> | undefined;
watch(callSearch, (q) => {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(() => (filters.value = { ...filters.value, q: q.trim() || undefined }), 250);
});
onBeforeUnmount(() => clearTimeout(searchTimer));
watch(
  () => [r.value?.since, r.value?.range === 'day' && r.value.date !== today.value ? r.value.until : undefined] as const,
  ([since, until]) => {
    if (since !== filters.value.since || until !== filters.value.until) filters.value = { ...filters.value, since: since ?? undefined, until: until ?? undefined };
  },
  { immediate: true },
);
const focusLabel = ref('');
const failedOnly = computed({ get: () => filters.value.ok === false, set: (v: boolean) => (filters.value = { ...filters.value, ok: v ? false : undefined }) });
const narrowed = computed(() => !!(filters.value.model || filters.value.provider || filters.value.origin));
function clearFocus() {
  filters.value = { ...filters.value, model: undefined, provider: undefined, account: undefined, origin: undefined };
  focusLabel.value = '';
}
const calls = useCalls(filters);
const callRows = computed<CallRecord[]>(() => calls.data.value?.pages.flatMap((p) => p.calls) ?? []);
// Another filter is another list: it fades through once its rows are in (not while the old ones stand in).
const callsKey = ref(JSON.stringify(filters.value));
watch(
  () => [calls.isPlaceholderData.value, JSON.stringify(filters.value)] as const,
  ([placeholder, key]) => placeholder || (callsKey.value = key),
);
const callColumns = computed<Column[]>(() => [
  { key: 'at', label: t('usage.at'), width: '96px' },
  { key: 'model', label: t('usage.model') },
  { key: 'route', label: t('usage.route'), width: 'minmax(0, 0.8fr)', minWidth: 760 },
  // Left out on a phone before the model is: there the time, the model and the status are what fit.
  { key: 'tokens', label: t('usage.tokens'), align: 'end', width: '80px', minWidth: 440 },
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
const chosenIndex = computed(() => (chosen.value ? callRows.value.findIndex((c) => c.id === chosen.value!.id) : -1));
const hasNewer = computed(() => chosenIndex.value > 0);
const hasOlder = computed(() => chosenIndex.value >= 0 && (chosenIndex.value < callRows.value.length - 1 || !!calls.hasNextPage.value));
function newer() {
  if (hasNewer.value) chosen.value = callRows.value[chosenIndex.value - 1];
}
async function older() {
  const at = chosenIndex.value;
  if (at < 0) return;
  if (at === callRows.value.length - 1 && calls.hasNextPage.value) await calls.fetchNextPage();
  const next = callRows.value[at + 1];
  if (next) chosen.value = next;
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
watch(slice, () => (detailOpen.value = false));
</script>

<template>
  <div class="usage">
    <Toolbar>
      <SegmentedControl v-model="range" :options="rangeOptions" :label="t('usage.range')" />
      <Transition :name="TRANSITIONS.fadeThrough">
        <div v-if="range === 'day'" class="day-nav" role="group" :aria-label="t('usage.dayNav')">
          <IconButton :icon="icons.ChevronLeft" :label="t('usage.previousDay')" :disabled="!!firstDay && day <= firstDay" @click="stepDay(-1)" />
          <DatePicker
            v-slot="{ toggle }"
            v-model="pickedDay"
            :max="today"
            :min="firstDay"
            :marks="marks"
            :label="t('usage.pickDay')"
            :today-label="t('usage.today')"
            :previous-label="t('usage.previousMonth')"
            :next-label="t('usage.nextMonth')"
          >
            <AppButton variant="text" :icon="icons.CalendarDays" class="day-button" @click="toggle">{{ dayTitle }}</AppButton>
          </DatePicker>
          <IconButton :icon="icons.ChevronRight" :label="t('usage.nextDay')" :disabled="day >= today" @click="stepDay(1)" />
        </div>
      </Transition>
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
    <div class="loading" aria-hidden="true"><ProgressBar v-if="loading" /></div>

    <!-- The first report, an empty range and the report itself replace one another with a fade
         through, as when the first call of a quiet day comes in. -->
    <FadeSwap>
      <div v-if="report.isPending.value" key="pending" class="tiles"><Skeleton v-for="i in 6" :key="i" height="136px" shape="medium" /></div>
      <EmptyState v-else-if="empty" key="empty" :icon="icons.BarChart3" :title="t('usage.empty')" :text="t('usage.emptyText')" />
      <div v-else-if="r" key="report" class="report">
        <div class="tiles">
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

        <Collapse :show="live.active.length > 0">
          <PageSection :title="t('live.running', { n: live.active.length })" :icon="icons.Radio">
            <TransitionGroup :name="TRANSITIONS.list" tag="div" class="now" v-bind="keepLeavingInPlace">
              <LiveCallCard v-for="(c, i) in live.active" :key="c.id" :call="c" :style="staggerStyle(i)" />
            </TransitionGroup>
          </PageSection>
        </Collapse>

        <PageSection :title="t('usage.overTime')" :subtitle="shownRange === 'day' ? longDay(shownDay ?? today) : t(`usage.granularity.${granularity}`)" :icon="icons.BarChart3">
          <template v-if="granularity === 'day'" #actions><span class="type-body-small muted">{{ t('usage.openDayHint') }}</span></template>
          <AxisPanes :value="chartKey" :order="chartOrder">
            <div :key="chartKey">
              <p v-if="hourlyMissing" class="type-body-medium muted note">{{ t('usage.noHourly') }}</p>
              <StackedBarChart
                v-else
                :series="stackSeries"
                :points="stackPoints"
                :label="t('usage.overTime')"
                :format="compact"
                :total-label="t('usage.total')"
                :selectable="granularity === 'day'"
                @select="openDay"
              />
            </div>
          </AxisPanes>
        </PageSection>

        <Collapse :show="hasSpeed">
          <PageSection :title="t('usage.speed')" :subtitle="t('usage.speedHint')" :icon="icons.Gauge">
            <AxisPanes :value="chartKey" :order="chartOrder">
              <div :key="chartKey" class="speed">
                <div class="speed-chart">
                  <h3 class="type-title-small">{{ t('usage.ttftAvg') }}</h3>
                  <LineChart :series="ttftSeries" :x-labels="xLabels" :format="(n) => duration(n)" :height="180" />
                </div>
                <div class="speed-chart">
                  <h3 class="type-title-small">{{ t('usage.tpsAvg') }}</h3>
                  <LineChart :series="tpsSeries" :x-labels="xLabels" :format="(n) => String(Math.round(n))" :height="180" />
                </div>
              </div>
            </AxisPanes>
          </PageSection>
        </Collapse>

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
              <template #cell-calls="{ row }"><TweenNumber :value="row.calls" /></template>
              <template #cell-tokens="{ row }"><TweenNumber :value="row.input + row.output" :format="compact" /></template>
              <template #cell-output="{ row }"><TweenNumber :value="row.output" :format="compact" /></template>
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
                  <TweenNumber class="type-body-small" :value="ratio(row.cache_read, row.input)" :format="(n) => percent(n, 0)" />
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
            <!-- The list's own filters: a row under the title, the search first and widest, then the
                 chips that narrow it. Not in the title's actions, which hold a few compact buttons. -->
            <div class="call-filters" role="search" :aria-label="t('usage.searchCalls')">
              <SearchField v-model="callSearch" compact class="call-search" :label="t('usage.searchCalls')" :placeholder="t('usage.searchCalls')" />
              <div class="call-chips">
                <Chip :label="t('usage.failedOnly')" :icon="icons.CircleX" :selected="failedOnly" @click="failedOnly = !failedOnly" />
                <Chip v-if="narrowed" :label="focusLabel" selected removable @remove="clearFocus" @click="clearFocus" />
              </div>
            </div>
            <FadeSwap>
              <DataTable
                :key="callsKey"
                :columns="callColumns"
                :rows="callRows"
                :row-key="(row) => row.id"
                :label="t('usage.calls')"
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
            </FadeSwap>
          </PageSection>
        </div>
      </div>
    </FadeSwap>

    <CallDetailSheet v-model:open="detailOpen" :call="chosen" :has-newer="hasNewer" :has-older="hasOlder" @newer="newer" @older="older" />
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
.usage { display: flex; flex-direction: column; gap: var(--app-space-4); padding: var(--app-space-4) var(--app-space-6) var(--app-space-8); padding-inline-end: calc(var(--app-space-6) - var(--app-scroll-lane)); min-width: 0; }
.report { display: flex; flex-direction: column; gap: var(--app-space-4); min-width: 0; }
.tiles { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: var(--app-space-3); }
/* The bar takes no room: it lies along the toolbar's foot while a report is on its way. */
.loading { height: 0; margin-top: calc(-1 * var(--app-space-4) + 2px); position: relative; }
.loading > * { position: absolute; inset-inline: 0; top: 0; }
.day-nav { display: inline-flex; align-items: center; gap: var(--app-space-1); }
.day-button { min-width: 0; }
.note { margin: 0; padding: var(--app-space-6) 0; text-align: center; }
.call-filters { display: flex; flex-wrap: wrap; align-items: center; gap: var(--app-space-2) var(--app-space-3); }
/* Grows to a comfortable width, then stops; on a narrow screen it takes the row and the chips wrap under it. */
.call-search { flex: 1 1 240px; max-width: 400px; }
.call-chips { display: flex; flex-wrap: wrap; align-items: center; gap: var(--app-space-2); min-width: 0; }
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
  .usage { padding: var(--app-space-3); padding-inline-end: max(var(--app-space-1), calc(var(--app-space-3) - var(--app-scroll-lane))); }
  .tiles { grid-template-columns: repeat(2, minmax(0, 1fr)); gap: var(--app-space-2); }
}
</style>
