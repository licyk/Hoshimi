<script setup lang="ts">
import type { Component } from 'vue';
import AppIcon from '@/ui/AppIcon.vue';

/**
 * One section of a page: a surface with a title row (an optional icon, the title and a line under
 * it, the section's own actions at the end) and its content. Every page is a single column of
 * these, so titles, padding and actions line up from page to page.
 */
withDefaults(defineProps<{ title?: string; subtitle?: string; icon?: Component; as?: string }>(), { title: '', subtitle: '', icon: undefined, as: 'section' });
</script>

<template>
  <component :is="as" class="page-section">
    <header v-if="title || $slots.actions" class="head">
      <AppIcon v-if="icon" :icon="icon" :size="20" class="icon" />
      <div class="titles">
        <h2 v-if="title" class="type-title-medium title">{{ title }}</h2>
        <p v-if="subtitle" class="type-body-small muted subtitle">{{ subtitle }}</p>
      </div>
      <div v-if="$slots.actions" class="actions"><slot name="actions" /></div>
    </header>
    <slot />
  </component>
</template>

<style scoped>
.page-section {
  --app-surface-bg: var(--md-sys-color-surface);
  display: flex; flex-direction: column; gap: var(--app-space-3); min-width: 0; padding: var(--app-space-4) var(--app-space-6) var(--app-space-5);
  border-radius: var(--md-sys-shape-corner-large); background: var(--app-surface-bg); color: var(--md-sys-color-on-surface);
}
.head { display: flex; align-items: center; gap: var(--app-space-3); min-height: 40px; flex-wrap: wrap; }
.icon { color: var(--md-sys-color-on-surface-variant); }
.titles { display: flex; flex-direction: column; flex: 1 1 200px; min-width: 0; }
.title, .subtitle { margin: 0; overflow-wrap: anywhere; }
.actions { display: flex; align-items: center; gap: var(--app-space-2); flex-wrap: wrap; margin-left: auto; }
@container app-content (max-width: 599px) {
  .page-section { padding: var(--app-space-3) var(--app-space-4) var(--app-space-4); }
}
</style>
