<script setup lang="ts">
import { computed } from 'vue';

/**
 * A call's arguments, one to a row: a short value beside its name, a long or many-lined one (a
 * prompt, a document) under it as it was written, and a list or an object as JSON.
 */
const props = defineProps<{ args: Record<string, unknown> }>();
const rows = computed(() =>
  Object.entries(props.args).map(([key, value]) => {
    const text = typeof value === 'string' ? value : JSON.stringify(value, null, 2);
    const block = typeof value === 'object' && value !== null ? true : text.includes('\n') || text.length > 80;
    return { key, text, block, kind: typeof value === 'string' ? 'string' : value === null ? 'null' : typeof value };
  }),
);
</script>

<template>
  <dl v-if="rows.length" class="params type-body-small">
    <div v-for="row in rows" :key="row.key" class="row" :class="{ block: row.block }">
      <dt>{{ row.key }}</dt>
      <dd><pre v-if="row.block" class="value app-scroll app-scroll-chain app-scroll-auto">{{ row.text }}</pre><code v-else class="value" :class="row.kind">{{ row.text }}</code></dd>
    </div>
  </dl>
</template>

<style scoped>
.params {
  display: grid; grid-template-columns: auto minmax(0, 1fr); gap: var(--app-space-1) var(--app-space-3); margin: 0; padding: var(--app-space-2) var(--app-space-3);
  border: 1px solid var(--md-sys-color-outline-variant); border-radius: var(--md-sys-shape-corner-small); background: var(--md-sys-color-surface-container-high);
}
.row { display: contents; }
dt { color: var(--md-sys-color-on-surface-variant); font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }
dd { min-width: 0; margin: 0; }
.row.block dt { grid-column: 1 / -1; }
.row.block dd { grid-column: 1 / -1; }
.value { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; color: var(--md-sys-color-on-surface); overflow-wrap: anywhere; }
code.value.number, code.value.boolean, code.value.null { color: var(--md-sys-color-secondary); }
pre.value {
  max-height: 240px; margin: 0; padding: var(--app-space-2); border-radius: var(--md-sys-shape-corner-extra-small);
  background: var(--md-sys-color-surface-container-highest); white-space: pre-wrap;
}
</style>
