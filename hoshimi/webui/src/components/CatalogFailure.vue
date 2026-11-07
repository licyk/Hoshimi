<script setup lang="ts">
import { computed } from 'vue';
import { useI18n } from '@/i18n';
import { ErrorNotice } from '@/ui';

const props = defineProps<{ kind: string; status?: number | null; host?: string }>();
const { t } = useI18n();
const hint = computed(() => {
  if (props.kind === 'catalog_endpoint_missing') return 'endpoint';
  if (['authentication', 'permission_denied'].includes(props.kind)) return 'credentials';
  if (['timeout', 'proxy_error', 'connection_error'].includes(props.kind)) return 'network';
  if (['challenge', 'edge_rejected', 'region_restricted'].includes(props.kind)) return 'access';
  if (props.kind === 'invalid_response') return 'response';
  if (props.kind === 'rate_limited') return 'rate';
  if (props.kind === 'quota_exhausted') return 'quota';
  return 'generic';
});
</script>

<template>
  <ErrorNotice :message="[t(`network.errors.${kind}`), status ? `HTTP ${status}` : '', host].filter(Boolean).join(' · ')" :hint="t(`catalog.hints.${hint}`)">
    <template v-if="$slots.meta" #meta><slot name="meta" /></template>
    <template v-if="$slots.actions" #actions><slot name="actions" /></template>
  </ErrorNotice>
</template>
