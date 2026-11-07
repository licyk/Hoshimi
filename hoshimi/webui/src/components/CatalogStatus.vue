<script setup lang="ts">
import { computed } from 'vue';
import { useCatalog } from '@/api/catalog';
import CatalogFailure from '@/components/CatalogFailure.vue';
import { useI18n } from '@/i18n';
import { AppButton, ErrorNotice } from '@/ui';

const catalog = useCatalog();
const { t } = useI18n();
const total = computed(() => catalog.data.value?.providers.length ?? 0);
const loaded = computed(() => catalog.data.value?.providers.filter((p) => p.status !== 'loading').length ?? 0);
const failures = computed(() => catalog.data.value?.providers.filter((p) => p.status === 'failed' || p.status === 'timeout') ?? []);
</script>

<template>
  <div v-if="catalog.isFetching.value || failures.length || catalog.isError.value" class="catalog-status">
    <span v-if="catalog.isFetching.value" role="status">{{ t('catalog.loading', { loaded, total }) }}</span>
    <CatalogFailure
      v-for="p in failures" :key="p.id"
      :title="t('catalog.failed', { providers: p.id })"
      :kind="p.error ?? (p.status === 'timeout' ? 'timeout' : 'catalog_failed')" :status="p.diagnostic?.status" :host="p.diagnostic?.host"
    />
    <ErrorNotice v-if="catalog.isError.value" :message="t('catalog.interrupted')" />
    <div v-if="!catalog.isFetching.value" class="actions"><AppButton variant="text" @click="catalog.refetch()">{{ t('common.retry') }}</AppButton></div>
  </div>
</template>

<style scoped>
.catalog-status { display: flex; flex-direction: column; gap: var(--app-space-2); min-width: 0; color: var(--md-sys-color-on-surface-variant); font-size: var(--md-sys-typescale-body-small-size); }
.actions { align-self: flex-start; }
</style>
