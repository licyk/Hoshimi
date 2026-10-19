<script setup lang="ts">
import { computed } from 'vue';
import { useStats } from '@/api/queries/app';
import { dayLabel, grouped, millis, rate, when } from '@/format';
import { useI18n } from '@/i18n';
import { BarChart, Badge, DataList, EmptyState, IconButton, Skeleton, Surface, icons } from '@/ui';

const { t } = useI18n();
const stats = useStats();

const grand = computed(() => stats.data.value?.grand);
const days = computed(() => stats.data.value?.days ?? []);
const models = computed(() => stats.data.value?.models ?? []);
const samples = computed(() => stats.data.value?.samples ?? []);
const bars = computed(() => days.value.slice(-30).map((d) => ({ label: dayLabel(d.day), value: d.total })));
const empty = computed(() => !days.value.length && !models.value.length && !samples.value.length);
const outputOf = (row: { input: number; output: number }) => grouped(row.input + row.output);
</script>

<template>
  <div class="usage">
    <div class="head">
      <IconButton :icon="icons.RefreshCw" :label="t('common.refresh')" :spin="stats.isFetching.value" @click="stats.refetch()" />
    </div>

    <div v-if="stats.isPending.value" class="skeletons"><Skeleton v-for="i in 3" :key="i" height="160px" shape="medium" /></div>
    <EmptyState v-else-if="empty" :icon="icons.BarChart3" :title="t('usage.empty')" :text="t('usage.emptyText')" />
    <template v-else>
      <Surface :level="0" shape="large" class="section">
        <h2 class="type-title-large">{{ t('usage.totals') }}</h2>
        <DataList
          :rows="[
            { label: t('usage.input'), value: grouped(grand?.input) },
            { label: t('usage.output'), value: grouped(grand?.output) },
            { label: t('usage.reasoning'), value: grouped(grand?.reasoning) },
            { label: t('usage.calls'), value: grouped(grand?.calls) },
            { label: t('usage.failed'), value: grouped(grand?.failed) },
            { label: t('usage.days'), value: grouped(days.length) },
          ]"
        />
      </Surface>

      <Surface v-if="bars.length" :level="0" shape="large" class="section">
        <div class="section-head">
          <h2 class="type-title-large">{{ t('usage.daily') }}</h2>
          <span class="type-body-small muted">{{ t('usage.dailyUnits') }}</span>
        </div>
        <BarChart :bars="bars" :label="t('usage.daily')" />
      </Surface>

      <Surface v-if="models.length" :level="0" shape="large" class="section">
        <h2 class="type-title-large">{{ t('usage.byModel') }}</h2>
        <div class="cols type-label-small muted" aria-hidden="true">
          <span>{{ t('usage.model') }}</span>
          <span class="num">{{ t('usage.tokens') }}</span>
          <span class="num">{{ t('usage.ttft') }}</span>
          <span class="num">{{ t('usage.tps') }}</span>
        </div>
        <ul class="list" role="list">
          <li v-for="m in models" :key="m.model" class="row">
            <div class="row-text">
              <span class="id mono type-body-medium">{{ m.model }}</span>
              <span class="sub type-body-small muted">{{ m.name }} · {{ t('usage.callCount', { n: m.calls }) }}</span>
            </div>
            <span class="num type-body-medium">{{ outputOf(m) }}</span>
            <span class="num type-body-medium">{{ millis(m.avg_ttft_ms) }}</span>
            <span class="num type-body-medium">{{ rate(m.tps) }}</span>
            <Badge v-if="m.failed" tone="error" :value="m.failed" />
          </li>
        </ul>
      </Surface>

      <Surface v-if="samples.length" :level="0" shape="large" class="section">
        <div class="section-head">
          <h2 class="type-title-large">{{ t('usage.recent') }}</h2>
          <span class="type-body-small muted">{{ t('usage.recentHint') }}</span>
        </div>
        <div class="cols type-label-small muted" aria-hidden="true">
          <span>{{ t('usage.model') }}</span>
          <span class="num">{{ t('usage.ttft') }}</span>
          <span class="num">{{ t('usage.decode') }}</span>
          <span class="num">{{ t('usage.tps') }}</span>
        </div>
        <ul class="list" role="list">
          <li v-for="(s, i) in samples" :key="i" class="row">
            <div class="row-text">
              <span class="id mono type-body-medium">{{ s.model }}</span>
              <span class="sub type-body-small muted">
                {{ when(s.at) }} · {{ t(`usage.origin_${s.origin}`) }}
                <template v-if="s.effort"> · {{ s.effort }}</template>
                <template v-if="s.no_usage"> · {{ t('usage.noUsage') }}</template>
              </span>
            </div>
            <span class="num type-body-medium">{{ millis(s.ttft_ms) }}</span>
            <span class="num type-body-medium">{{ millis(s.decode_ms) }}</span>
            <span class="num type-body-medium">{{ rate(s.tps) }}</span>
            <Badge :tone="s.ok ? 'primary' : 'error'" :value="s.ok ? t('usage.served') : t('usage.failed')" />
          </li>
        </ul>
      </Surface>
    </template>
  </div>
</template>

<style scoped>
.usage { display: flex; flex-direction: column; gap: var(--app-space-4); width: 100%; padding: var(--app-space-4) var(--app-space-6) var(--app-space-8); }
.head { display: flex; align-items: center; justify-content: flex-end; }
h1, h2, p { margin: 0; }
.skeletons { display: flex; flex-direction: column; gap: var(--app-space-4); }
.section { display: flex; flex-direction: column; gap: var(--app-space-2); padding: var(--app-space-4) var(--app-space-6) var(--app-space-6); }
.section-head { display: flex; align-items: baseline; justify-content: space-between; gap: var(--app-space-3); flex-wrap: wrap; }
/* Model, then the three measured columns: the head and every row share the track sizes. */
.cols, .row { display: grid; grid-template-columns: minmax(0, 1fr) 72px 72px 72px; gap: var(--app-space-3); align-items: center; }
.cols { padding: 0 var(--app-space-3); }
.list { display: flex; flex-direction: column; gap: var(--app-space-1); margin: 0; padding: 0; list-style: none; }
.row { padding: var(--app-space-2) var(--app-space-3); border-radius: var(--md-sys-shape-corner-small); background: var(--md-sys-color-surface-container); }
.row-text { min-width: 0; display: flex; flex-direction: column; }
.id, .sub { overflow-wrap: anywhere; }
.num { text-align: right; white-space: nowrap; }
.mono { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }
@media (max-width: 599px) {
  .usage { padding: var(--app-space-3); }
  .section { padding: var(--app-space-4); }
  .cols, .row { grid-template-columns: minmax(0, 1fr) 56px 56px 56px; gap: var(--app-space-2); }
}
</style>
