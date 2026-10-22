<script setup lang="ts">
import type { Component } from 'vue';
import { AppIcon } from '@/ui';

/** A small round button in a message's row of actions: an icon, named by ``label`` (also its tooltip). */
defineProps<{ icon: Component; label: string; disabled?: boolean }>();
defineEmits<{ click: [MouseEvent] }>();
</script>

<template>
  <button type="button" class="action state-layer" :aria-label="label" :title="label" :disabled="disabled" @click="$emit('click', $event)">
    <AppIcon :icon="icon" :size="18" />
  </button>
</template>

<style scoped>
.action {
  display: grid; place-items: center; width: 32px; height: 32px; padding: 0; border: 0; border-radius: var(--md-sys-shape-corner-small);
  background: transparent; color: var(--md-sys-color-on-surface-variant); cursor: pointer;
}
.action:disabled { opacity: var(--app-disabled-content-opacity); cursor: default; }
.action:disabled::before { display: none; }
</style>
