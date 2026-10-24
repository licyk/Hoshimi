<script setup lang="ts">
/**
 * A tool's words as they came, for what no view lays out: an error, a call's arguments that are not
 * an object, output in a shape the view does not know. Long output scrolls down, and a swipe past
 * its end goes on to the conversation.
 */
defineProps<{ text: string; failed?: boolean }>();
</script>

<template>
  <pre class="raw-output app-scroll app-scroll-chain app-scroll-auto type-body-small" :class="{ failed }">{{ text }}</pre>
</template>

<style scoped>
.raw-output {
  max-height: 320px; margin: 0; padding: var(--app-space-2) var(--app-space-3); border-radius: var(--md-sys-shape-corner-small);
  background: var(--md-sys-color-surface-container-highest); color: var(--md-sys-color-on-surface);
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace; white-space: pre-wrap; overflow-wrap: anywhere;
}
.raw-output.failed { background: var(--md-sys-color-error-container); color: var(--md-sys-color-on-error-container); }
</style>
