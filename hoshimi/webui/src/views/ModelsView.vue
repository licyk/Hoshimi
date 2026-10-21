<script setup lang="ts">
import { computed, ref, watch } from 'vue';
import { useRouter } from 'vue-router';
import { useModelSpecs, useModels, useProbe, useProviders, useStartProbe } from '@/api/queries/app';
import { PROBE_STATES, contextWindow, probeTone } from '@/format';
import { useI18n } from '@/i18n';
import CatalogStatus from '@/components/CatalogStatus.vue';
import { AppButton, AppIcon, Badge, DataTable, EmptyState, IconButton, PageSection, SearchField, SelectField, Skeleton, Toolbar, Tooltip, icons, useSnackbar, type Column, type SortState } from '@/ui';

const { t } = useI18n();
const router = useRouter();
const models = useModels();
const specs = useModelSpecs();
const providers = useProviders();
const probe = useProbe();
const startProbe = useStartProbe();
const snackbar = useSnackbar();

/** One row: what a client may call, and what the last round decided about it. */
interface Row {
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

const rows = computed<Row[]>(() => {
  const byId = new Map((specs.data.value?.data ?? []).map((s) => [s.id, s]));
  const toRow = (id: string, owner: string): Row => {
    const s = byId.get(id);
    return {
      id,
      owner,
      name: s?.name ?? '',
      vision: s?.vision ?? false,
      reasoning: s?.reasoning ?? false,
      tools: s?.tools ?? null,
      // Not `?? false`: an unstated price is not a paid price, and a badge the
      // gateway cannot back would be a promise about the operator's money.
      free: s?.free ?? null,
      context: s?.context_window ?? 0,
      efforts: s?.efforts.length ?? 0,
      state: s?.probe?.state ?? null,
      unavailable: s?.unavailable ?? false,
    };
  };
  const out = (models.data.value?.data ?? []).map((m) => toRow(m.id, m.owned_by));
  const listed = new Set(out.map((r) => r.id));
  // A spec the OpenAI catalogue has not listed yet still exists: keep it visible rather than
  // hide a model the gateway would answer for.
  for (const s of specs.data.value?.data ?? []) if (!listed.has(s.id)) out.push(toRow(s.id, s.provider));
  return out;
});
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

// One grid for every model: long ids are cut with the full id on hover, never wrapping the row
// into a stack of badges; what a model can do is three small marks, its verdict one badge.
const sort = ref<SortState | null>(null);
const columns = computed<Column[]>(() => [
  { key: 'id', label: t('models.id'), sortable: true },
  { key: 'owner', label: t('models.owner'), width: 'minmax(0, 0.5fr)', sortable: true, minWidth: 720 },
  { key: 'context', label: t('models.context'), align: 'end', width: '80px', sortable: true, minWidth: 900 },
  { key: 'caps', label: t('models.capabilities'), width: '112px', minWidth: 560 },
  { key: 'state', label: t('models.availability'), width: '120px', sortable: true },
  { key: 'actions', label: '', width: '96px' },
]);
const capabilityList = (m: Row) => [
  ...(m.reasoning ? [{ key: 'reasoning', icon: icons.Brain, label: m.efforts ? `${t('models.reasoning')} · ${t('models.effortCount', { n: m.efforts })}` : t('models.reasoning') }] : []),
  ...(m.vision ? [{ key: 'vision', icon: icons.Eye, label: t('models.vision') }] : []),
  ...(m.tools ? [{ key: 'tools', icon: icons.Wrench, label: t('models.tools') }] : []),
];
const stateValue = (m: Row) => (m.unavailable ? 'unavailable' : (m.state ?? ''));
const tableValue = (m: Row, key: string): unknown => (key === 'state' ? stateValue(m) : key === 'context' ? m.context || null : (m as unknown as Record<string, unknown>)[key]);
</script>

<template>
  <div class="models">
    <Toolbar>
      <SearchField v-model="query" class="search" :label="t('models.search')" :placeholder="t('models.searchPlaceholder')" />
      <SelectField v-model="owner" class="filter" :label="t('models.filter')" :options="ownerOptions" />
      <SelectField v-if="stateOptions.length > 1" v-model="state" class="filter" :label="t('models.availability')" :options="stateOptions" />
      <template #end>
        <AppButton variant="tonal" :icon="icons.ScanSearch" :loading="running" @click="runProbe">
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
      <DataTable v-else v-model:sort="sort" :columns="columns" :rows="visible" :row-key="(m) => m.id" :value="tableValue" :label="t('models.title')" :row-height="56">
        <template #cell-id="{ row }">
          <span class="name-cell">
            <span class="text mono" :title="row.id">{{ row.id }}</span>
            <span class="type-body-small muted text">
              <template v-if="row.name && row.name !== row.id">{{ row.name }}</template>
              <template v-if="row.free === true"> · {{ t('models.free') }}</template>
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
          <!-- The ban outranks the round: it says what the gateway will do to the next call,
               whereas the round only says what the last one did. -->
          <Badge v-if="row.unavailable" tone="error" :value="t('models.states.unavailable')" />
          <Badge v-else-if="row.state" :tone="probeTone(row.state)" :value="t(`models.states.${row.state}`)" />
          <span v-else class="type-body-small muted">{{ t('models.states.none') }}</span>
        </template>
        <template #cell-actions="{ row }">
          <IconButton :icon="icons.Copy" :label="t('models.copyId')" @click="copy(row.id)" />
          <IconButton :icon="icons.Sparkles" :label="t('models.use')" @click="openInPlayground(row.id)" />
        </template>
      </DataTable>
    </PageSection>
  </div>
</template>

<style scoped>
.models { display: flex; flex-direction: column; gap: var(--app-space-4); width: 100%; padding: var(--app-space-4) var(--app-space-6) var(--app-space-8); min-width: 0; }
.search { flex: 1 1 280px; min-width: 0; }
.filter { flex: 0 1 220px; }
.skeletons { display: flex; flex-direction: column; gap: var(--app-space-1); }
.name-cell { display: flex; flex-direction: column; min-width: 0; }
.text { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.caps { display: inline-flex; align-items: center; gap: var(--app-space-2); color: var(--md-sys-color-on-surface-variant); }
@container app-content (max-width: 599px) {
  .models { padding: var(--app-space-3); }
}
</style>
