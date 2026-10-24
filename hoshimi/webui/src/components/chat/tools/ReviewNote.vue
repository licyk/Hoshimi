<script setup lang="ts">
import type { Component } from 'vue';
import { AppIcon } from '@/ui';

/** What the reviewer said of a call: allowed or denied, the risk it saw, and why. */
defineProps<{ review: { icon: Component; tone: string; text: string; why: string; risk: string } }>();
</script>

<template>
  <p class="review type-body-small" :class="review.tone">
    <AppIcon :icon="review.icon" :size="18" /><span><strong>{{ review.text }}</strong><span v-if="review.risk"> · {{ review.risk }}</span><span v-if="review.why"> — {{ review.why }}</span></span>
  </p>
</template>

<style scoped>
.review { display: flex; align-items: flex-start; gap: var(--app-space-2); margin: 0; color: var(--md-sys-color-on-surface-variant); }
.review :deep(svg) { flex: none; }
.review.done :deep(svg) { color: var(--md-sys-color-primary); }
.review.failed :deep(svg) { color: var(--md-sys-color-error); }
.review.ask :deep(svg) { color: var(--md-sys-color-tertiary); }
.review strong { color: var(--md-sys-color-on-surface); font-weight: 500; }
</style>
