<script setup lang="ts">
import { BASE_URL } from '@/api/baseUrl';
import { useCatalog } from '@/api/catalog';
import CatalogStatus from '@/components/CatalogStatus.vue';
import { useMeta, useProviders, useVersion } from '@/api/queries/app';
import { useI18n } from '@/i18n';
import { AppButton, Badge, DataList, EmptyState, IconButton, Skeleton, Surface, icons, useSnackbar } from '@/ui';

const { t } = useI18n();
const meta = useMeta();
const version = useVersion();
const providers = useProviders();
const catalog = useCatalog();
const modelCount = (id: string) => {
  const row = catalog.data.value?.providers.find((p) => p.id === id);
  if (row) return row.status === 'ready' || row.data.length ? row.data.length : '…';
  return providers.data.value?.find((p) => p.id === id)?.models ?? '…';
};
const snackbar = useSnackbar();

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
    <Surface :level="0" shape="large" class="section">
      <h2 class="type-title-large">{{ t('overview.title') }}</h2>
      <div v-if="meta.isPending.value" class="skeletons"><Skeleton v-for="i in 2" :key="i" height="56px" /></div>
      <DataList
        v-else
        :rows="[
          { label: t('overview.version'), value: version.data.value?.version ?? meta.data.value?.version ?? '—' },
          { label: t('overview.defaultModel'), value: meta.data.value?.default_model ?? t('overview.noDefaultModel') },
          { label: t('overview.providers'), value: `${meta.data.value?.providers ?? 0} · ${t('common.accounts')}: ${meta.data.value?.accounts ?? 0}` },
        ]"
      />
      <p class="type-body-small muted">{{ meta.data.value?.auth_required ? t('overview.authRequired') : t('overview.authOptional') }}</p>
    </Surface>

    <Surface :level="0" shape="large" class="section">
      <div class="section-head">
        <h2 class="type-title-large">{{ t('overview.endpoints') }}</h2>
        <AppButton v-if="meta.data.value && !meta.data.value.auth_required" variant="text" :icon="icons.ExternalLink" @click="openDocs">{{ t('overview.apiDocs') }}</AppButton>
      </div>
      <ul class="endpoints">
        <li v-for="e in endpoints" :key="e.key" class="endpoint">
          <span class="path mono type-body-medium">{{ e.path }}</span>
          <span class="label type-body-small muted">{{ t(`overview.${e.key}`) }}</span>
          <IconButton :icon="icons.Copy" :label="t('common.copy')" @click="copy(`${BASE_URL}${e.path}`)" />
        </li>
      </ul>
      <p class="type-body-small muted">{{ t('overview.baseURL') }}: <span class="mono">{{ BASE_URL || '/' }}</span></p>
    </Surface>

    <Surface :level="0" shape="large" class="section">
      <h2 class="type-title-large">{{ t('overview.providers') }}</h2>
      <CatalogStatus />
      <div v-if="providers.isPending.value" class="skeletons"><Skeleton v-for="i in 2" :key="i" height="72px" /></div>
      <EmptyState v-else-if="!providers.data.value?.length" :icon="icons.Layers" :title="t('overview.noProviders')" :text="t('overview.noProvidersText')" />
      <div v-else class="providers">
        <div v-for="p in providers.data.value" :key="p.id" class="provider">
          <span class="name type-title-small">{{ p.display_name }}</span>
          <span class="meta type-body-small muted">
            {{ p.accounts ? t('overview.providerAccounts', { accounts: p.accounts, models: modelCount(p.id) }) : t('overview.emptyAccounts') }}
          </span>
          <Badge :tone="p.enabled ? 'primary' : 'neutral'" :value="p.enabled ? t('common.enabled') : t('common.disabled')" />
        </div>
      </div>
    </Surface>
  </div>
</template>

<style scoped>
/* One column of sections, each as wide as the window allows. */
.overview { display: flex; flex-direction: column; gap: var(--app-space-4); width: 100%; padding: var(--app-space-4) var(--app-space-6) var(--app-space-8); }
h1, h2 { margin: 0; }
p { margin: 0; }
.section { display: flex; flex-direction: column; gap: var(--app-space-2); padding: var(--app-space-4) var(--app-space-6) var(--app-space-6); }
.section-head { display: flex; align-items: center; justify-content: space-between; gap: var(--app-space-3); flex-wrap: wrap; }
.skeletons { display: flex; flex-direction: column; gap: var(--app-space-2); }
.endpoints { display: flex; flex-direction: column; gap: var(--app-space-1); margin: 0; padding: 0; list-style: none; }
.endpoint { display: flex; align-items: center; gap: var(--app-space-3); padding: var(--app-space-1) var(--app-space-2); border-radius: var(--md-sys-shape-corner-small); background: var(--md-sys-color-surface-container); }
.path { flex: 1; min-width: 0; overflow-wrap: anywhere; }
.label { white-space: nowrap; }
.providers { display: grid; grid-template-columns: repeat(auto-fill, minmax(280px, 1fr)); gap: var(--app-space-3); }
.provider { display: flex; flex-direction: column; gap: var(--app-space-1); min-width: 0; padding: var(--app-space-3); border-radius: var(--md-sys-shape-corner-medium); background: var(--md-sys-color-surface-container); }
.name { min-width: 0; overflow-wrap: anywhere; }
.mono { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }
@media (max-width: 599px) {
  .overview { padding: var(--app-space-3); }
  .section { padding: var(--app-space-4); }
}
</style>
