<script setup lang="ts">
import { computed } from 'vue';
import { useCatalog } from '@/api/catalog';
import { useI18n } from '@/i18n';
import { AppButton } from '@/ui';

const catalog = useCatalog();
const { t } = useI18n();
const total = computed(() => catalog.data.value?.providers.length ?? 0);
const loaded = computed(() => catalog.data.value?.providers.filter((p) => p.status !== 'loading').length ?? 0);
const failed = computed(() => catalog.data.value?.providers.filter((p) => p.status === 'failed' || p.status === 'timeout').map((p) => p.id).join(', ') ?? '');
</script>

<template>
  <div v-if="catalog.isFetching.value || failed || catalog.isError.value" class="catalog-status" role="status" aria-live="polite">
    <span v-if="catalog.isFetching.value">{{ t('catalog.loading', { loaded, total }) }}</span>
    <span v-if="failed" class="failures">{{ t('catalog.failed', { providers: failed }) }}</span>
    <span v-if="catalog.isError.value" class="failures">{{ t('catalog.interrupted') }}</span>
    <AppButton v-if="!catalog.isFetching.value" variant="text" @click="catalog.refetch()">{{ t('common.retry') }}</AppButton>
  </div>
</template>

<style scoped>
.catalog-status { display: flex; align-items: center; flex-wrap: wrap; gap: var(--app-space-2); color: var(--md-sys-color-on-surface-variant); font-size: var(--md-sys-typescale-body-small-size); }
.failures { color: var(--md-sys-color-error); overflow-wrap: anywhere; }
</style>
