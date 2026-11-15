<script setup lang="ts">
import { computed } from 'vue';
import { useCallDetail } from '@/api/queries/usage';
import CallPayload from '@/components/CallPayload.vue';
import { useI18n } from '@/i18n';
import { AppButton, ErrorNotice } from '@/ui';

const props = defineProps<{ seq: number }>();
const { t } = useI18n();
const { data, isPending, error, refetch } = useCallDetail(computed(() => props.seq));
const trace = computed(() => data.value?.trace);
</script>

<template>
  <section class="payloads">
    <h3 class="type-title-small">{{ t('usage.payloads') }}</h3>
    <p class="type-body-small muted">{{ t('usage.payloadHint') }}</p>
    <p v-if="isPending" role="status">{{ t('common.loading') }}</p>
    <ErrorNotice v-else-if="error" :message="error.message">
      <template #actions><AppButton variant="text" @click="refetch()">{{ t('common.retry') }}</AppButton></template>
    </ErrorNotice>
    <template v-else-if="trace">
      <CallPayload v-if="trace.client_request" :title="t('usage.clientRequest')" :value="trace.client_request" :incomplete="trace.client_request.complete === false" />
      <CallPayload v-if="trace.client_response" :title="t('usage.clientResponse')" :value="trace.client_response" :incomplete="trace.client_response.complete === false" />
      <CallPayload v-if="trace.request" :title="t('usage.modelRequest')" :value="trace.request" />
      <CallPayload :title="t('usage.modelResponse')" :value="trace.response" :incomplete="trace.response.complete === false" />
      <template v-if="trace.upstream.length">
        <CallPayload v-for="(attempt, index) in trace.upstream" :key="index" :title="`${t('usage.upstreamExchange')} ${index + 1}`" :value="attempt" />
      </template>
      <p v-else class="type-body-small muted">{{ t('usage.noUpstreamCapture') }}</p>
    </template>
    <p v-else class="type-body-small muted">{{ t('usage.noPayloads') }}</p>
  </section>
</template>

<style scoped>
.payloads { display: flex; flex-direction: column; gap: var(--app-space-3); min-width: 0; }
h3, p { margin: 0; }
</style>
