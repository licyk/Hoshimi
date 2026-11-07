<script setup lang="ts">
import { ErrorNotice } from '@/ui';
/**
 * A tool's words as they came, for what no view lays out: an error, a call's arguments that are not
 * an object, output in a shape the view does not know. Long output scrolls down, and a swipe past
 * its end goes on to the conversation.
 */
defineProps<{ text: string; failed?: boolean }>();
</script>

<template>
  <ErrorNotice v-if="failed" compact announcement="off">
    <pre class="raw-output failed app-scroll app-scroll-chain app-scroll-auto type-body-small">{{ text }}</pre>
  </ErrorNotice>
  <pre v-else class="raw-output app-scroll app-scroll-chain app-scroll-auto type-body-small">{{ text }}</pre>
</template>

<style scoped>
.raw-output {
  max-height: 320px; margin: 0; padding: var(--app-space-2) var(--app-space-3); border-radius: var(--md-sys-shape-corner-small);
  background: var(--md-sys-color-surface-container-highest); color: var(--md-sys-color-on-surface);
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace; white-space: pre-wrap; overflow-wrap: anywhere;
  padding-inline-end: calc(var(--app-space-3) + var(--app-scroll-overlay));
}
.raw-output.failed { padding: 0; background: transparent; color: inherit; padding-inline-end: var(--app-scroll-overlay); }
</style>
