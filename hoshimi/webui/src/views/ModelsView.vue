<script setup lang="ts">
import { computed, ref, watch } from 'vue';
import { useRouter } from 'vue-router';
import { useModelSpecs, useModels, useProbe, useProviders, useStartProbe } from '@/api/queries/app';
import { PROBE_STATES, contextWindow, probeTone } from '@/format';
import { useI18n } from '@/i18n';
import CatalogStatus from '@/components/CatalogStatus.vue';
import { AppButton, Badge, EmptyState, IconButton, SearchField, SelectField, Skeleton, Surface, icons, useSnackbar } from '@/ui';

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
</script>

<template>
  <div class="models">
    <div class="head">
      <p class="type-body-medium muted">
        {{ t('models.count', { n: rows.length }) }}
        <template v-if="available"> · {{ t('models.availableCount', { n: available }) }}</template>
      </p>
      <div class="head-actions">
        <AppButton variant="tonal" :icon="icons.ScanSearch" :loading="running" @click="runProbe">
          {{ running ? t('models.probeRunning') : t('models.probe') }}
        </AppButton>
        <IconButton :icon="icons.RefreshCw" :label="t('common.refresh')" :spin="models.isFetching.value" @click="models.refetch()" />
      </div>
    </div>

    <CatalogStatus />

    <div class="filters">
      <SearchField v-model="query" class="search" :placeholder="t('models.searchPlaceholder')" />
      <SelectField v-model="owner" class="filter" :label="t('models.filter')" :options="ownerOptions" />
      <SelectField v-if="stateOptions.length > 1" v-model="state" class="filter" :label="t('models.availability')" :options="stateOptions" />
    </div>

    <Surface :level="0" shape="large" class="section">
      <div v-if="!rows.length && models.isFetching.value" class="list">
        <Skeleton v-for="i in 5" :key="i" height="44px" shape="medium" />
      </div>
      <EmptyState
        v-else-if="!rows.length"
        :icon="icons.Layers"
        :title="t('models.empty')"
        :text="t('models.emptyText')"
      />
      <EmptyState v-else-if="!visible.length" :icon="icons.Search" :title="t('models.none')" :text="t('models.noneText')" />
      <ul v-else class="list" role="list">
        <li v-for="m in visible" :key="m.id" class="row">
          <div class="row-text">
            <span class="id mono type-body-medium">{{ m.id }}</span>
            <span class="owner type-body-small muted">
              {{ m.name && m.name !== m.id ? `${m.name} · ` : '' }}{{ displayName(m.owner) || m.owner || '—' }}
              <template v-if="m.context"> · {{ contextWindow(m.context) }}</template>
            </span>
          </div>
          <!-- The ban outranks the round: it says what the gateway will do to the
               next call, whereas the round only says what the last one did. -->
          <Badge v-if="m.unavailable" tone="error" :value="t('models.states.unavailable')" />
          <Badge v-else-if="m.state" :tone="probeTone(m.state)" :value="t(`models.states.${m.state}`)" />
          <!-- `=== true`, not truthiness: an unstated price is not a paid one,
               and the gateway shows a badge it can actually stand behind. -->
          <Badge v-if="m.free === true" tone="primary" :value="t('models.free')" />
          <Badge v-if="m.reasoning" tone="neutral" :value="t('models.reasoning')" />
          <Badge v-if="m.vision" tone="neutral" :value="t('models.vision')" />
          <Badge v-if="m.tools !== null" tone="neutral" :value="t(m.tools ? 'models.tools' : 'models.noTools')" />
          <Badge v-if="m.efforts" tone="neutral" :value="t('models.effortCount', { n: m.efforts })" />
          <IconButton :icon="icons.Copy" :label="t('models.copyId')" @click="copy(m.id)" />
          <AppButton variant="tonal" :icon="icons.Sparkles" @click="openInPlayground(m.id)">{{ t('models.use') }}</AppButton>
        </li>
      </ul>
    </Surface>
  </div>
</template>

<style scoped>
.models { display: flex; flex-direction: column; gap: var(--app-space-4); width: 100%; padding: var(--app-space-4) var(--app-space-6) var(--app-space-8); }
.head { display: flex; align-items: center; justify-content: space-between; gap: var(--app-space-4); flex-wrap: wrap; }
.head-actions { display: flex; align-items: center; gap: var(--app-space-2); }
h1 { margin: 0; }
p { margin: 0; }
.filters { display: flex; align-items: flex-start; gap: var(--app-space-3); flex-wrap: wrap; }
.search { flex: 1 1 320px; min-width: 0; }
.filter { flex: 0 1 240px; }
.section { padding: var(--app-space-3); }
.list { display: flex; flex-direction: column; gap: var(--app-space-1); margin: 0; padding: 0; list-style: none; }
.row { display: flex; align-items: center; gap: var(--app-space-3); padding: var(--app-space-2) var(--app-space-3); border-radius: var(--md-sys-shape-corner-small); background: var(--md-sys-color-surface-container); }
.row-text { flex: 1; min-width: 0; display: flex; flex-direction: column; }
.id { overflow-wrap: anywhere; }
.owner { overflow-wrap: anywhere; }
.mono { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }
@media (max-width: 599px) {
  .models { padding: var(--app-space-3); }
}
</style>
