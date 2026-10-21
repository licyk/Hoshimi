<script setup lang="ts">
import { computed } from 'vue';
import { useRouter } from 'vue-router';
import LiveCallCard from '@/components/LiveCallCard.vue';
import { compact, duration, percent, ratio, when } from '@/format';
import { useI18n } from '@/i18n';
import { useLiveStore } from '@/stores/live';
import { AppButton, Badge, EmptyState, LiveDot, SideSheet, TRANSITIONS, icons, keepLeavingInPlace, staggerStyle } from '@/ui';

/**
 * What the gateway is doing right now, from anywhere in the interface: the calls running (each
 * with its phase and numbers, arriving and leaving with the list motion), today's totals, and the
 * calls that just finished. The Usage page has the full picture; this is the glance.
 */
const open = defineModel<boolean>('open', { default: false });
const { t } = useI18n();
const live = useLiveStore();
const router = useRouter();
const stateLabel = computed(() => t(`live.states.${live.state}`));
const today = computed(() => live.today);
const hitRate = computed(() => (today.value ? ratio(today.value.cache_read, today.value.input) : null));
function openUsage() {
  open.value = false;
  void router.push('/usage');
}
</script>

<template>
  <SideSheet v-model:open="open" :title="t('live.title')" :close-label="t('common.close')">
    <template #header-actions>
      <LiveDot :state="live.state" :label="stateLabel" />
    </template>
    <div class="live-sheet">
      <dl v-if="today" class="today">
        <div><dt class="type-label-medium muted">{{ t('live.todayTokens') }}</dt><dd class="type-title-large">{{ compact(today.input + today.output) }}</dd></div>
        <div><dt class="type-label-medium muted">{{ t('usage.calls') }}</dt><dd class="type-title-large">{{ compact(today.calls) }}</dd></div>
        <div><dt class="type-label-medium muted">{{ t('usage.cacheHit') }}</dt><dd class="type-title-large">{{ percent(hitRate) }}</dd></div>
      </dl>

      <h3 class="type-title-small heading">{{ t('live.running', { n: live.active.length }) }}</h3>
      <EmptyState v-if="!live.active.length" :icon="icons.Activity" :title="t('live.none')" :text="t('live.noneText')" />
      <TransitionGroup v-else :name="TRANSITIONS.list" tag="div" class="list" v-bind="keepLeavingInPlace">
        <LiveCallCard v-for="(c, i) in live.active" :key="c.id" :call="c" :style="staggerStyle(i)" />
      </TransitionGroup>

      <template v-if="live.recent.length">
        <h3 class="type-title-small heading">{{ t('live.finished') }}</h3>
        <TransitionGroup :name="TRANSITIONS.list" tag="ul" class="recent" v-bind="keepLeavingInPlace">
          <li v-for="r in live.recent.slice(0, 10)" :key="r.id" class="row">
            <div class="text">
              <span class="type-body-medium name" :title="r.model">{{ r.name || r.model }}</span>
              <span class="type-body-small muted sub">{{ when(r.at) }} · {{ duration(r.elapsed_ms) }}</span>
            </div>
            <span class="type-body-medium num">{{ compact(r.input + r.output) }}</span>
            <Badge :tone="r.ok ? 'primary' : 'error'" :value="r.ok ? t('usage.served') : t('usage.failedOne')" />
          </li>
        </TransitionGroup>
      </template>
      <AppButton variant="tonal" :icon="icons.BarChart3" class="more" @click="openUsage">{{ t('live.openUsage') }}</AppButton>
    </div>
  </SideSheet>
</template>

<style scoped>
.live-sheet { display: flex; flex-direction: column; gap: var(--app-space-3); }
.today { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: var(--app-space-2); margin: 0; padding: var(--app-space-3) var(--app-space-4); border-radius: var(--md-sys-shape-corner-medium); background: var(--md-sys-color-secondary-container); color: var(--md-sys-color-on-secondary-container); }
.today div { display: flex; flex-direction: column; min-width: 0; }
.today dt { color: inherit; opacity: 0.8; }
.today dd { margin: 0; }
.heading { margin: var(--app-space-2) 0 0; }
.list { position: relative; display: flex; flex-direction: column; gap: var(--app-space-2); }
.recent { position: relative; display: flex; flex-direction: column; gap: var(--app-space-1); margin: 0; padding: 0; list-style: none; }
.row { display: flex; align-items: center; gap: var(--app-space-3); padding: var(--app-space-2) var(--app-space-3); border-radius: var(--md-sys-shape-corner-small); background: var(--md-sys-color-surface-container); }
.text { display: flex; flex-direction: column; flex: 1; min-width: 0; }
.name, .sub { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.more { align-self: flex-start; margin-top: var(--app-space-2); }
</style>
