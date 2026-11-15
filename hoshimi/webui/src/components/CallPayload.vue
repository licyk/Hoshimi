<script setup lang="ts">
import { computed, ref } from 'vue';
import { useI18n } from '@/i18n';
import { AppButton } from '@/ui';

const props = defineProps<{ title: string; value: unknown; incomplete?: boolean }>();
const { t } = useI18n();
const copied = ref(false);
const failed = ref(false);
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
  copied.value = false;
  failed.value = false;
  try { await navigator.clipboard.writeText(text.value); copied.value = true; } catch { failed.value = true; }
}
</script>

<template>
  <details class="payload">
    <summary class="type-title-small">{{ title }} <span v-if="incomplete" class="partial">{{ t('usage.payloadIncomplete') }}</span></summary>
    <div class="actions">
      <AppButton variant="text" @click="copy">{{ copied ? t('common.copied') : t('common.copy') }}</AppButton>
      <span v-if="failed" role="status">{{ t('usage.payloadCopyFailed') }}</span>
    </div>
    <pre tabindex="0" :aria-label="title">{{ text }}</pre>
  </details>
</template>

<style scoped>
.payload { min-width: 0; border: 1px solid var(--md-sys-color-outline-variant); border-radius: var(--md-sys-shape-corner-small); padding: var(--app-space-3); }
summary { cursor: pointer; overflow-wrap: anywhere; }
.actions { display: flex; align-items: center; justify-content: flex-end; }
.partial { color: var(--md-sys-color-error); }
pre { margin: 0; max-height: 28rem; overflow: auto; white-space: pre-wrap; overflow-wrap: anywhere; font-size: 12px; user-select: text; }
</style>
