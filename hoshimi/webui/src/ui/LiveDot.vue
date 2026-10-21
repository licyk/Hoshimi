<script setup lang="ts">
/**
 * The state of a live connection, in a word and a dot: live (pulsing), connecting, offline. The
 * word always travels with the dot, so the state never rests on colour alone.
 */
defineProps<{ state: 'live' | 'connecting' | 'offline'; label: string }>();
</script>

<template>
  <span class="live-dot type-label-medium" :class="state" role="status">
    <span class="dot" aria-hidden="true" />
    <span>{{ label }}</span>
  </span>
</template>

<style scoped>
.live-dot {
  display: inline-flex; align-items: center; gap: var(--app-space-2); height: 32px; padding: 0 var(--app-space-3);
  border-radius: var(--md-sys-shape-corner-full); background: var(--md-sys-color-surface-container-high); color: var(--md-sys-color-on-surface-variant); white-space: nowrap;
}
.dot { position: relative; width: 8px; height: 8px; border-radius: 50%; background: var(--md-sys-color-outline); }
.live .dot { background: var(--md-sys-color-primary); }
.live .dot::after {
  content: ''; position: absolute; inset: 0; border-radius: inherit; background: inherit;
  animation: live-pulse 1.8s var(--md-sys-motion-easing-standard) infinite;
}
.connecting .dot { background: var(--md-sys-color-tertiary); animation: live-blink 1.2s linear infinite; }
.offline .dot { background: var(--md-sys-color-error); }
@keyframes live-pulse { from { transform: scale(1); opacity: 0.6; } to { transform: scale(2.6); opacity: 0; } }
@keyframes live-blink { 50% { opacity: 0.3; } }
</style>
