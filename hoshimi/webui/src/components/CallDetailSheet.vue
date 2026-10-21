<script setup lang="ts">
import { computed } from 'vue';
import type { CallRecord } from '@/api/types';
import { duration, grouped, percent, ratio, when } from '@/format';
import { useI18n } from '@/i18n';
import { Badge, DataList, Meter, SideSheet } from '@/ui';

/** Everything recorded about one call: where it went, every token count and every timing. */
const props = defineProps<{ call: CallRecord | null }>();
const open = defineModel<boolean>('open', { default: false });
const { t } = useI18n();
const c = computed(() => props.call);
const uncached = computed(() => (c.value ? Math.max(0, c.value.input - c.value.cache_read - c.value.cache_write) : 0));
</script>

<template>
  <SideSheet v-model:open="open" :title="t('usage.callDetail')" :close-label="t('common.close')">
    <div v-if="c" class="detail">
      <div class="head">
        <span class="type-title-medium name">{{ c.name || c.model }}</span>
        <Badge :tone="c.ok ? 'primary' : 'error'" :value="c.ok ? t('usage.served') : t('usage.failedOne')" />
      </div>
      <p v-if="c.error_message" class="type-body-medium error-text">{{ c.error_code ? `${c.error_code}: ` : '' }}{{ c.error_message }}</p>
      <DataList
        :rows="[
          { label: t('usage.at'), value: when(c.at) },
          { label: t('usage.model'), value: c.model, mono: true },
          { label: t('usage.provider'), value: c.provider || '—' },
          { label: t('usage.account'), value: c.account || '—', mono: true },
          { label: t('usage.origin'), value: t(`usage.origins.${c.origin}`) },
          { label: t('usage.effort'), value: c.effort || '—' },
          { label: t('usage.mode'), value: c.stream ? t('usage.streamed') : t('usage.whole') },
          { label: t('usage.attempts'), value: c.attempts },
          { label: t('usage.finish'), value: c.finish_reason || '—' },
        ]"
      />
      <h3 class="type-title-small">{{ t('usage.tokens') }}</h3>
      <Meter
        :label="t('usage.inputSplit')"
        :segments="[
          { label: t('usage.series.uncached'), value: uncached, slot: 1 },
          { label: t('usage.series.cacheRead'), value: c.cache_read, slot: 2 },
          { label: t('usage.series.cacheWrite'), value: c.cache_write, slot: 3 },
        ]"
      />
      <DataList
        :rows="[
          { label: t('usage.input'), value: grouped(c.input) },
          { label: t('usage.series.uncached'), value: grouped(uncached) },
          { label: t('usage.series.cacheRead'), value: `${grouped(c.cache_read)} (${percent(ratio(c.cache_read, c.input))})` },
          { label: t('usage.series.cacheWrite'), value: grouped(c.cache_write) },
          { label: t('usage.output'), value: grouped(c.output) },
          { label: t('usage.reasoning'), value: grouped(c.reasoning) },
        ]"
      />
      <p v-if="c.no_usage" class="type-body-small muted">{{ t('usage.noUsage') }}</p>
      <h3 class="type-title-small">{{ t('usage.timing') }}</h3>
      <DataList
        :rows="[
          { label: t('usage.ttft'), value: duration(c.ttft_ms) },
          { label: t('usage.decode'), value: c.decode_ms ? duration(c.decode_ms) : '—' },
          { label: t('usage.elapsed'), value: duration(c.elapsed_ms) },
          { label: t('usage.tps'), value: c.tps ?? '—' },
        ]"
      />
    </div>
  </SideSheet>
</template>

<style scoped>
.detail { display: flex; flex-direction: column; gap: var(--app-space-3); }
.head { display: flex; align-items: center; gap: var(--app-space-2); min-width: 0; }
.name { flex: 1; min-width: 0; overflow-wrap: anywhere; }
h3, p { margin: 0; }
.error-text { color: var(--md-sys-color-error); overflow-wrap: anywhere; }
</style>
