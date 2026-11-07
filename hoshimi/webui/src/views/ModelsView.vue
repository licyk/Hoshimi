<script setup lang="ts">
import { computed, ref, watch } from 'vue';
import { useRouter } from 'vue-router';
import { modelKey } from '@/api/catalog';
import { useModelSpecs, useProbe, useProviders, useStartProbe } from '@/api/queries/app';
import { PROBE_STATES, contextWindow, probeTone } from '@/format';
import { useI18n } from '@/i18n';
import { useWindowClass } from '@/theme/breakpoints';
import CatalogStatus from '@/components/CatalogStatus.vue';
import { AppButton, AppIcon, AppMenu, Badge, DataTable, EmptyState, IconButton, PageSection, SearchField, SelectField, Skeleton, Toolbar, Tooltip, icons, useSnackbar, type Column, type SortState } from '@/ui';

const { t } = useI18n();
const router = useRouter();
const models = useModelSpecs();
const providers = useProviders();
const probe = useProbe();
const startProbe = useStartProbe();
const snackbar = useSnackbar();

/** One row: what a client may call, and what the last round decided about it. */
interface Row {
  key: string;
  id: string;
  owner: string;
  name: string;
  vision: boolean;
  reasoning: boolean;
  tools: boolean | null;
  /** null is the station never saying, which is most ids at most stations. */
  free: boolean | null;
  context: number;
  efforts: number;
  state: string | null;
  /** The gateway's own ban: the station answered 404/410 for every account. */
  unavailable: boolean;
}

const rows = computed<Row[]>(() => (models.data.value?.data ?? []).map((s) => ({
  key: modelKey(s),
  id: s.id,
  owner: s.provider,
  name: s.name,
  vision: s.vision,
  reasoning: s.reasoning,
  tools: s.tools,
  free: s.free,
  context: s.context_window,
  efforts: s.efforts.length,
  state: s.probe?.state ?? null,
  unavailable: s.unavailable,
})));
const available = computed(() => rows.value.filter((r) => r.state === 'available').length);

const query = ref('');
const owner = ref<string | null>(null);
const state = ref('');
const visible = computed(() => {
  const q = query.value.trim().toLowerCase();
  return rows.value.filter(
    (r) =>
      (!q || r.id.toLowerCase().includes(q) || r.owner.toLowerCase().includes(q) || r.name.toLowerCase().includes(q)) &&
      (!owner.value || r.owner === owner.value) &&
      (!state.value || (state.value === 'none' ? !r.state : r.state === state.value)),
  );
});
const ownerOptions = computed(() => {
  const owners = [...new Set(rows.value.map((r) => r.owner))].sort();
  return [{ value: '', label: t('models.allProviders') }, ...owners.map((o) => ({ value: o, label: o || '—' }))];
});
/** Only the verdicts a round has actually handed down are worth offering. */
const stateOptions = computed(() => {
  const present = PROBE_STATES.filter((s) => rows.value.some((r) => r.state === s));
  const options = [{ value: '', label: t('models.allStates') }];
  if (rows.value.some((r) => !r.state)) options.push({ value: 'none', label: t('models.states.none') });
  return [...options, ...present.map((s) => ({ value: s, label: t(`models.states.${s}`) }))];
});
const displayName = (id: string): string | undefined => providers.data.value?.find((p) => id.startsWith(`${p.id}/`) || p.id === id)?.display_name;

const running = computed(() => startProbe.isPending.value || !!probe.data.value?.running);
/** A round only changes what the rows say when it ends. */
watch(
  () => probe.data.value?.running,
  (runningNow, wasRunning) => {
    if (wasRunning && !runningNow) {
      void models.refetch();
      snackbar.show(t('models.probeDone'));
    }
  },
);
async function runProbe() {
  try {
    await startProbe.mutateAsync();
    snackbar.show(t('models.probeStarted'));
  } catch (error) {
    snackbar.error(error instanceof Error ? error.message : t('common.error'));
  }
}

async function copy(text: string) {
  try {
    await navigator.clipboard.writeText(text);
    snackbar.show(t('common.copied'));
  } catch {
    snackbar.error(t('common.error'));
  }
}
const openInPlayground = (id: string) => router.push({ path: '/playground', query: { model: id } });
// On a phone the toolbar's Probe is its icon alone and a row's two actions fold into one menu, so the
// search keeps its row with the actions and the model id keeps room to be read.
const windowClass = useWindowClass();
const phone = computed(() => windowClass.value === 'compact');
const rowMenu = computed(() => [
  { id: 'copy', label: t('models.copyId'), icon: icons.Copy },
  { id: 'use', label: t('models.use'), icon: icons.Sparkles },
]);
const onRowMenu = (id: string, model: string) => (id === 'copy' ? copy(model) : openInPlayground(model));

// One grid for every model: long ids are cut with the full id on hover, never wrapping the row
// into a stack of badges; what a model can do is three small marks, its verdict one badge. On a
// phone the verdict moves under the id, which then has the row's width but for the menu.
const sort = ref<SortState | null>(null);
const columns = computed<Column[]>(() => [
  { key: 'id', label: t('models.id'), sortable: true },
  { key: 'owner', label: t('models.owner'), width: 'minmax(0, 0.5fr)', sortable: true, minWidth: 720 },
  { key: 'context', label: t('models.context'), align: 'end', width: '80px', sortable: true, minWidth: 900 },
  { key: 'caps', label: t('models.capabilities'), width: '112px', minWidth: 560 },
  ...(phone.value ? [] : [{ key: 'state', label: t('models.availability'), width: '120px', sortable: true }]),
  { key: 'actions', label: '', width: phone.value ? '48px' : '96px' },
]);
const capabilityList = (m: Row) => [
  ...(m.reasoning ? [{ key: 'reasoning', icon: icons.Brain, label: m.efforts ? `${t('models.reasoning')} · ${t('models.effortCount', { n: m.efforts })}` : t('models.reasoning') }] : []),
  ...(m.vision ? [{ key: 'vision', icon: icons.Eye, label: t('models.vision') }] : []),
  ...(m.tools ? [{ key: 'tools', icon: icons.Wrench, label: t('models.tools') }] : []),
];
const stateValue = (m: Row) => (m.unavailable ? 'unavailable' : (m.state ?? ''));
/** The verdict as a badge, or null before any round. The ban outranks the round: it says what the
 *  gateway will do to the next call, whereas the round only says what the last one did. */
const verdict = (m: Row) =>
  m.unavailable ? { tone: 'error' as const, label: t('models.states.unavailable') } : m.state ? { tone: probeTone(m.state), label: t(`models.states.${m.state}`) } : null;
const tableValue = (m: Row, key: string): unknown => (key === 'state' ? stateValue(m) : key === 'context' ? m.context || null : (m as unknown as Record<string, unknown>)[key]);
</script>

<template>
  <div class="models">
    <Toolbar>
      <SearchField v-model="query" class="search" :label="t('models.search')" :placeholder="t('models.searchPlaceholder')" />
      <SelectField v-model="owner" class="filter" :label="t('models.filter')" :options="ownerOptions" />
      <SelectField v-if="stateOptions.length > 1" v-model="state" class="filter" :label="t('models.availability')" :options="stateOptions" />
      <template #end>
        <IconButton v-if="phone" tonal :icon="running ? icons.Loader2 : icons.ScanSearch" :spin="running" :disabled="running" :label="running ? t('models.probeRunning') : t('models.probe')" @click="runProbe" />
        <AppButton v-else variant="tonal" :icon="icons.ScanSearch" :loading="running" @click="runProbe">
          {{ running ? t('models.probeRunning') : t('models.probe') }}
        </AppButton>
        <IconButton :icon="icons.RefreshCw" :label="t('common.refresh')" :spin="models.isFetching.value" @click="models.refetch()" />
      </template>
    </Toolbar>

    <CatalogStatus />

    <PageSection :title="t('models.title')" :subtitle="available ? `${t('models.count', { n: rows.length })} · ${t('models.availableCount', { n: available })}` : t('models.count', { n: rows.length })" :icon="icons.Layers">
      <div v-if="!rows.length && models.isFetching.value" class="skeletons">
        <Skeleton v-for="i in 5" :key="i" height="44px" shape="medium" />
      </div>
      <EmptyState v-else-if="!rows.length" :icon="icons.Layers" :title="t('models.empty')" :text="t('models.emptyText')" />
      <EmptyState v-else-if="!visible.length" :icon="icons.Search" :title="t('models.none')" :text="t('models.noneText')" />
      <DataTable v-else v-model:sort="sort" :columns="columns" :rows="visible" :row-key="(m) => m.key" :value="tableValue" :label="t('models.title')" :row-height="56">
        <template #cell-id="{ row }">
          <span class="name-cell">
            <span class="text mono" :title="row.id">{{ row.id }}</span>
            <span class="sub">
              <!-- On a phone a verdict is the badge before the name; a model no round has reached shows none. -->
              <Badge v-if="phone && verdict(row)" :tone="verdict(row)!.tone" :value="verdict(row)!.label" class="verdict" />
              <span class="type-body-small muted text">
                <template v-if="phone">{{ displayName(row.owner) || row.owner }}</template>
                <template v-if="row.name && row.name !== row.id">{{ phone ? ' · ' : '' }}{{ row.name }}</template>
                <template v-if="row.free === true"> · {{ t('models.free') }}</template>
              </span>
            </span>
          </span>
        </template>
        <template #cell-owner="{ row }"><span class="text muted">{{ displayName(row.owner) || row.owner || '—' }}</span></template>
        <template #cell-context="{ row }">{{ contextWindow(row.context) || '—' }}</template>
        <template #cell-caps="{ row }">
          <span class="caps">
            <Tooltip v-for="c in capabilityList(row)" :key="c.key" :text="c.label"><AppIcon :icon="c.icon" :size="18" :label="c.label" /></Tooltip>
            <span v-if="row.tools === false" class="type-body-small muted">{{ t('models.noTools') }}</span>
          </span>
        </template>
        <template #cell-state="{ row }">
          <Badge v-if="verdict(row)" :tone="verdict(row)!.tone" :value="verdict(row)!.label" />
          <span v-else class="type-body-small muted">{{ t('models.states.none') }}</span>
        </template>
        <template #cell-actions="{ row }">
          <AppMenu v-if="phone" :items="rowMenu" @select="onRowMenu($event, row.key)">
            <template #default="{ toggle }"><IconButton :icon="icons.MoreVertical" :label="t('common.more')" @click="toggle" /></template>
          </AppMenu>
          <template v-else>
            <IconButton :icon="icons.Copy" :label="t('models.copyId')" @click="copy(row.key)" />
            <IconButton :icon="icons.Sparkles" :label="t('models.use')" @click="openInPlayground(row.key)" />
          </template>
        </template>
      </DataTable>
    </PageSection>
  </div>
</template>

<style scoped>
.models { display: flex; flex-direction: column; gap: var(--app-space-4); padding: var(--app-space-4) var(--app-space-6) var(--app-space-8); padding-inline-end: calc(var(--app-space-6) - var(--app-scroll-lane)); min-width: 0; }
.search { flex: 1 1 280px; min-width: 0; }
.filter { flex: 0 1 220px; }
/* Too narrow for one row: the search and the actions share the first, the filters split the second
   (half of it each, so one filter alone does not stretch across). The search's 60% basis is what
   keeps the actions beside it and sends the first filter to the next row. */
@container app-content (max-width: 959px) {
  .toolbar :deep(.start) { display: contents; }
  .search { flex: 1 1 60%; }
  /* Sized by flex alone: md-outlined-select's box copies its host's width, min- and max-width, so a
     percentage max-width would shrink the box inside, and an auto width would fit it to its text. */
  .filter { order: 1; flex: 0 1 calc((100% - var(--app-space-3)) / 2); width: 100%; min-width: 0; }
}
.skeletons { display: flex; flex-direction: column; gap: var(--app-space-1); }
.name-cell { display: flex; flex-direction: column; min-width: 0; }
.sub { display: flex; align-items: center; gap: var(--app-space-2); min-width: 0; }
.verdict { flex: none; }
.text { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.caps { display: inline-flex; align-items: center; gap: var(--app-space-2); color: var(--md-sys-color-on-surface-variant); }
@container app-content (max-width: 599px) {
  .models { padding: var(--app-space-3); padding-inline-end: max(var(--app-space-1), calc(var(--app-space-3) - var(--app-scroll-lane))); }
}
</style>
