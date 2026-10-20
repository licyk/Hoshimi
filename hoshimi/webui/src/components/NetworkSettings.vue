<script setup lang="ts">
import { computed, ref, watch } from 'vue';
import { useI18n } from '@/i18n';
import { SelectField, Switch, TokenField } from '@/ui';

const props = defineProps<{
  value?: { mode?: string; proxy_configured?: boolean; system_proxy?: boolean };
  provider?: boolean;
}>();
const emit = defineEmits<{ save: [patch: Record<string, unknown>] }>();
const { t } = useI18n();
const draft = ref<string | null>(null);
watch(() => props.value, () => { draft.value = null; });
const mode = computed(() => draft.value ?? props.value?.mode ?? (props.provider ? 'inherit' : 'auto'));
const modes = computed(() => [
  ...(props.provider ? [{ value: 'inherit', label: t('network.inherit') }] : []),
  ...['auto', 'direct', 'proxy'].map((value) => ({ value, label: t(`network.${value}`) })),
]);
function changeMode(value: string | null) {
  if (value === 'proxy' && !props.value?.proxy_configured) { draft.value = value; return; }
  draft.value = null;
  emit('save', { mode: value });
}
</script>

<template>
  <div class="network-settings">
    <SelectField :model-value="mode" :label="t('network.route')" :options="modes" @update:model-value="changeMode" />
    <TokenField
      v-if="mode === 'proxy' || value?.proxy_configured"
      :label="t('network.proxyUrl')" :configured="value?.proxy_configured ?? false"
      :save-label="t('common.save')" :clear-label="t('common.clear')"
      :configured-text="t('network.proxyConfigured')" :not-configured-text="t('network.proxyHelp')"
      @save="emit('save', { mode: 'proxy', proxy: $event })"
      @clear="emit('save', { mode: provider ? 'inherit' : 'auto', proxy: null })"
    />
    <Switch v-if="mode === 'auto'" :model-value="value?.system_proxy ?? false" :label="t('network.systemProxy')" @update:model-value="emit('save', { system_proxy: $event })" />
  </div>
</template>

<style scoped>
.network-settings { display: grid; gap: var(--app-space-3); }
</style>
