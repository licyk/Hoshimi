<script setup lang="ts">
import { computed } from 'vue';
import CodeFrame from '@/components/chat/tools/CodeFrame.vue';
import ParamList from '@/components/chat/tools/ParamList.vue';
import RawOutput from '@/components/chat/tools/RawOutput.vue';
import { prettyJson } from '@/components/chat/tools/parse';
import { useI18n } from '@/i18n';
import { icons } from '@/ui';
import { highlight } from '@/ui/highlight';

/**
 * Any other tool: its arguments, one to a row (or as they were sent, when they are not an object),
 * and what it said, as highlighted JSON when that is what it is.
 */
const props = defineProps<{ raw: string; args: Record<string, unknown>; output: string; failed: boolean; pending: boolean }>();
const { t } = useI18n();
const json = computed(() => (props.failed ? null : prettyJson(props.output)));
const jsonHtml = computed(() => (json.value === null ? '' : (highlight(json.value, 'json') ?? '')));
const unparsed = computed(() => !props.pending && !Object.keys(props.args).length && props.raw.trim() && props.raw.trim() !== '{}');
</script>

<template>
  <ParamList v-if="Object.keys(args).length" :args="args" />
  <RawOutput v-else-if="unparsed" :text="raw" />
  <slot name="review" />
  <CodeFrame v-if="json !== null" :icon="icons.Braces" :title="t('playground.toolView.result')" sub="JSON" :copy="json">
    <pre class="json app-scroll app-scroll-chain app-scroll-auto"><code class="code-mono" v-html="jsonHtml" /></pre>
  </CodeFrame>
  <RawOutput v-else-if="output" :text="output" :failed="failed" />
</template>

<style scoped>
.json { max-height: 320px; margin: 0; padding: var(--app-space-2) var(--app-space-3); white-space: pre-wrap; overflow-wrap: anywhere; }
</style>
