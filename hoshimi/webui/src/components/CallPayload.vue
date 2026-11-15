<script setup lang="ts">
import { computed } from 'vue';
import RawOutput from '@/components/chat/tools/RawOutput.vue';
import { useI18n } from '@/i18n';
import { AppButton, Badge, ExpansionPanel, Toolbar, icons, useSnackbar } from '@/ui';

const props = defineProps<{ title: string; value: unknown; incomplete?: boolean }>();
const { t } = useI18n();
const snackbar = useSnackbar();
function format(value: unknown): string {
  if (typeof value === 'string') {
    try { return JSON.stringify(JSON.parse(value), null, 2); } catch { return value; }
  }
  if (value && typeof value === 'object' && 'body' in value && typeof value.body === 'string') {
    const { body, ...metadata } = value;
    return `${JSON.stringify(metadata, null, 2)}\n\n${format(body)}`;
  }
  if (value && typeof value === 'object' && 'request' in value && 'response_body' in value) {
    const { request, response_body, ...metadata } = value;
    return `${JSON.stringify(metadata, null, 2)}\n\n${t('usage.httpRequest')}:\n${format(request)}\n\n${t('usage.httpResponse')}:\n${format(response_body)}`;
  }
  return JSON.stringify(value, null, 2) ?? '';
}
const text = computed(() => format(props.value));
async function copy() {
  try {
    await navigator.clipboard.writeText(text.value);
    snackbar.show(t('common.copied'));
  } catch {
    snackbar.error(t('usage.payloadCopyFailed'));
  }
}
</script>

<template>
  <ExpansionPanel :label="title" :icon="icons.FileText">
    <template #trailing><Badge v-if="incomplete" tone="warning" :value="t('usage.payloadIncomplete')" /></template>
    <div class="payload-content">
      <Toolbar>
        <template #end><AppButton variant="text" :icon="icons.Copy" @click="copy">{{ t('common.copy') }}</AppButton></template>
      </Toolbar>
      <RawOutput :text="text" tabindex="0" :aria-label="title" />
    </div>
  </ExpansionPanel>
</template>

<style scoped>
.payload-content { display: flex; flex-direction: column; gap: var(--app-space-2); min-width: 0; }
</style>
