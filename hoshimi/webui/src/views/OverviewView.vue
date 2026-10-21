<script setup lang="ts">
import { computed, ref } from 'vue';
import { RouterLink } from 'vue-router';
import { BASE_URL } from '@/api/baseUrl';
import { useCatalog } from '@/api/catalog';
import { useMeta, useVersion } from '@/api/queries/app';
import { useChannels } from '@/api/queries/channels';
import { useUsageReport, type UsageSlice } from '@/api/queries/usage';
import CatalogStatus from '@/components/CatalogStatus.vue';
import { compact, percent, ratio } from '@/format';
import { useI18n } from '@/i18n';
import { useLiveStore } from '@/stores/live';
import { AppButton, Badge, DataList, EmptyState, IconButton, LiveDot, PageSection, Skeleton, Sparkline, icons, useSnackbar } from '@/ui';

const { t } = useI18n();
const meta = useMeta();
const version = useVersion();
const channels = useChannels();
const catalog = useCatalog();
const live = useLiveStore();
const snackbar = useSnackbar();
// The last day, hour by hour, for the trend under today's numbers.
const day = ref<UsageSlice>({ range: '24h' });
const report = useUsageReport(day);
const trend = computed(() => (report.data.value?.series ?? []).map((p) => p.input + p.output));
const today = computed(() => live.today);

const modelCount = (id: string) => {
  const row = catalog.data.value?.providers.find((p) => p.id === id);
  return row && (row.status === 'ready' || row.data.length) ? row.data.length : null;
};
// What is set up and in use first; the stations offered but not set up stay on the Channels page.
const configured = computed(() => (channels.data.value?.channels ?? []).filter((c) => c.status !== 'empty'));
const STATUS_TONE: Record<string, 'error' | 'warning' | 'primary' | 'neutral'> = { attention: 'warning', ok: 'primary', empty: 'neutral', disabled: 'neutral' };

/** Public OpenAI and Anthropic endpoints, as clients address them. */
const endpoints = [
  { key: 'chat', path: '/v1/chat/completions' },
  { key: 'responses', path: '/v1/responses' },
  { key: 'messages', path: '/v1/messages' },
  { key: 'countTokens', path: '/v1/messages/count_tokens' },
  { key: 'models', path: '/v1/models' },
];
async function copy(text: string) {
  try {
    await navigator.clipboard.writeText(text);
    snackbar.show(t('common.copied'));
  } catch {
    snackbar.error(t('common.error'));
  }
}
/**
 * The API reference is a plain page, so it cannot send the browser's stored key as a header.
 * It is offered only while the server asks for no key at all.
 */
const openDocs = () => window.open(`${BASE_URL}/docs`, '_blank', 'noopener');
</script>

<template>
  <div class="overview">
    <PageSection :title="t('overview.today')" :icon="icons.Activity">
      <template #actions>
        <LiveDot :state="live.state" :label="t(`live.states.${live.state}`)" />
        <RouterLink v-slot="{ navigate }" to="/usage" custom><AppButton variant="text" :icon="icons.BarChart3" @click="navigate">{{ t('overview.openUsage') }}</AppButton></RouterLink>
      </template>
      <div class="today">
        <dl class="figures">
          <div><dt class="type-label-medium muted">{{ t('usage.tokens') }}</dt><dd class="type-headline-small">{{ compact(today ? today.input + today.output : null) }}</dd></div>
          <div><dt class="type-label-medium muted">{{ t('usage.calls') }}</dt><dd class="type-headline-small">{{ compact(today?.calls) }}</dd></div>
          <div><dt class="type-label-medium muted">{{ t('usage.cacheHit') }}</dt><dd class="type-headline-small">{{ percent(today ? ratio(today.cache_read, today.input) : null) }}</dd></div>
          <div><dt class="type-label-medium muted">{{ t('overview.runningNow') }}</dt><dd class="type-headline-small">{{ live.active.length }}</dd></div>
        </dl>
        <Sparkline v-if="trend.length > 1" :values="trend" :height="48" :label="t('overview.last24h')" class="spark" />
      </div>
    </PageSection>

    <PageSection :title="t('overview.endpoints')" :subtitle="`${t('overview.baseURL')}: ${BASE_URL || '/'}`" :icon="icons.Link">
      <template #actions>
        <AppButton v-if="meta.data.value && !meta.data.value.auth_required" variant="text" :icon="icons.ExternalLink" @click="openDocs">{{ t('overview.apiDocs') }}</AppButton>
      </template>
      <ul class="endpoints">
        <li v-for="e in endpoints" :key="e.key" class="endpoint">
          <span class="path mono type-body-medium">{{ e.path }}</span>
          <span class="label type-body-small muted">{{ t(`overview.${e.key}`) }}</span>
          <IconButton :icon="icons.Copy" :label="t('common.copy')" @click="copy(`${BASE_URL}${e.path}`)" />
        </li>
      </ul>
      <p class="type-body-small muted">{{ meta.data.value?.auth_required ? t('overview.authRequired') : t('overview.authOptional') }}</p>
    </PageSection>

    <PageSection :title="t('overview.channels')" :icon="icons.Fingerprint">
      <template #actions>
        <RouterLink v-slot="{ navigate }" to="/channels" custom><AppButton variant="text" @click="navigate">{{ t('overview.manageChannels') }}</AppButton></RouterLink>
      </template>
      <CatalogStatus />
      <div v-if="channels.isPending.value" class="grid"><Skeleton v-for="i in 3" :key="i" height="72px" shape="medium" /></div>
      <EmptyState v-else-if="!configured.length" :icon="icons.Layers" :title="t('overview.noProviders')" :text="t('overview.noProvidersText')" />
      <div v-else class="grid">
        <RouterLink v-for="c in configured" :key="c.id" :to="`/channels/${c.id}`" class="channel state-layer">
          <span class="channel-head">
            <span class="type-title-small name">{{ c.display_name }}</span>
            <Badge :tone="STATUS_TONE[c.status]" :value="t(`channels.statuses.${c.status}`)" />
          </span>
          <span class="type-body-small muted">
            {{ c.accounts.length ? t('channels.accountCount', { n: c.accounts.length }) : t('channels.keyless') }}<template v-if="modelCount(c.id) !== null"> · {{ t('overview.modelCount', { n: modelCount(c.id)! }) }}</template><template v-if="c.usage_today.calls"> · {{ t('channels.tokensTodayN', { n: compact(c.usage_today.input + c.usage_today.output) }) }}</template>
          </span>
        </RouterLink>
      </div>
    </PageSection>

    <PageSection :title="t('overview.about')" :icon="icons.Info">
      <DataList
        :rows="[
          { label: t('overview.version'), value: version.data.value?.version ?? meta.data.value?.version ?? '—' },
          { label: t('overview.defaultModel'), value: meta.data.value?.default_model ?? t('overview.noDefaultModel') },
        ]"
      />
    </PageSection>
  </div>
</template>

<style scoped>
.overview { display: flex; flex-direction: column; gap: var(--app-space-4); width: 100%; padding: var(--app-space-4) var(--app-space-6) var(--app-space-8); min-width: 0; }
p { margin: 0; }
.today { display: grid; grid-template-columns: minmax(0, 1fr) minmax(160px, 320px); gap: var(--app-space-4); align-items: end; }
.figures { display: grid; grid-template-columns: repeat(auto-fit, minmax(110px, 1fr)); gap: var(--app-space-3); margin: 0; }
.figures div { display: flex; flex-direction: column; min-width: 0; }
.figures dd { margin: 0; }
.endpoints { display: flex; flex-direction: column; gap: var(--app-space-1); margin: 0; padding: 0; list-style: none; }
.endpoint { display: grid; grid-template-columns: minmax(0, 1fr) auto auto; align-items: center; gap: var(--app-space-3); padding: var(--app-space-1) var(--app-space-1) var(--app-space-1) var(--app-space-3); border-radius: var(--md-sys-shape-corner-small); background: var(--md-sys-color-surface-container); }
.path { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.label { white-space: nowrap; }
.grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(260px, 1fr)); gap: var(--app-space-2); }
.channel {
  display: flex; flex-direction: column; gap: var(--app-space-1); min-width: 0; padding: var(--app-space-3) var(--app-space-4);
  border-radius: var(--md-sys-shape-corner-medium); background: var(--md-sys-color-surface-container); color: var(--md-sys-color-on-surface); text-decoration: none;
}
.channel-head { display: flex; align-items: center; gap: var(--app-space-2); min-width: 0; }
.name { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
@container app-content (max-width: 599px) {
  .overview { padding: var(--app-space-3); }
  .today { grid-template-columns: minmax(0, 1fr); }
  .label { display: none; }
}
</style>
