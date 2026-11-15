<script setup lang="ts">
import { computed } from 'vue';
import { useCallDetail } from '@/api/queries/usage';
import CallPayload from '@/components/CallPayload.vue';
import { useI18n } from '@/i18n';
import { AppButton, EmptyState, ErrorNotice, FadeSwap, PageSection, Skeleton, icons } from '@/ui';

const props = defineProps<{ seq: number }>();
const { t } = useI18n();
const { data, isPending, error, refetch } = useCallDetail(computed(() => props.seq));
const trace = computed(() => data.value?.trace);
</script>

<template>
  <PageSection :title="t('usage.payloads')" :subtitle="t('usage.payloadHint')" :icon="icons.FileText">
    <FadeSwap>
      <div v-if="isPending" key="loading" class="payloads" role="status" :aria-label="t('common.loading')">
        <Skeleton v-for="index in 3" :key="index" height="40px" />
      </div>
      <ErrorNotice v-else-if="error" key="error" :message="error.message">
        <template #actions><AppButton variant="text" @click="refetch()">{{ t('common.retry') }}</AppButton></template>
      </ErrorNotice>
      <div v-else-if="trace" :key="data?.id" class="payloads">
        <CallPayload v-if="trace.client_request" :title="t('usage.clientRequest')" :value="trace.client_request" :incomplete="trace.client_request.complete === false" />
        <CallPayload v-if="trace.client_response" :title="t('usage.clientResponse')" :value="trace.client_response" :incomplete="trace.client_response.complete === false" />
        <CallPayload v-if="trace.request" :title="t('usage.modelRequest')" :value="trace.request" />
        <CallPayload :title="t('usage.modelResponse')" :value="trace.response" :incomplete="trace.response.complete === false" />
        <template v-if="trace.upstream.length">
          <CallPayload v-for="(attempt, index) in trace.upstream" :key="index" :title="`${t('usage.upstreamExchange')} ${index + 1}`" :value="attempt" />
        </template>
        <p v-else class="type-body-small muted">{{ t('usage.noUpstreamCapture') }}</p>
      </div>
      <EmptyState v-else key="empty" :icon="icons.FileText" :title="t('usage.payloads')" :text="t('usage.noPayloads')" />
    </FadeSwap>
  </PageSection>
</template>

<style scoped>
.payloads { display: flex; flex-direction: column; gap: var(--app-space-3); min-width: 0; }
p { margin: 0; }
</style>
