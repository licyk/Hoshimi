<script setup lang="ts">
import AppIcon from '@/ui/AppIcon.vue';
import { AlertTriangle } from '@/ui/icons';

/**
 * A recoverable failure where it happened: plain text, optional guidance and caller-owned actions.
 * Historical errors stay quiet; freshly submitted form failures can request an immediate announcement.
 * The default slot also accepts structured output such as a tool's scrollable log.
 */
withDefaults(defineProps<{
  title?: string;
  message?: string | null;
  hint?: string;
  compact?: boolean;
  announcement?: 'polite' | 'assertive' | 'off';
}>(), { title: '', message: '', hint: '', compact: false, announcement: 'polite' });
</script>

<template>
  <div
    class="error-notice"
    :class="{ compact }"
    :role="announcement === 'off' ? undefined : announcement === 'assertive' ? 'alert' : 'status'"
    :aria-live="announcement === 'off' ? undefined : announcement"
    :aria-atomic="announcement === 'off' ? undefined : true"
  >
    <AppIcon :icon="AlertTriangle" :size="compact ? 18 : 20" class="icon" />
    <div class="content">
      <p v-if="title" class="title type-label-large">{{ title }}</p>
      <p v-if="message" class="message" :class="compact ? 'type-body-small' : 'type-body-medium'">{{ message }}</p>
      <slot />
      <p v-if="hint" class="hint type-body-small">{{ hint }}</p>
      <div v-if="$slots.meta" class="meta type-body-small"><slot name="meta" /></div>
    </div>
    <div v-if="$slots.actions" class="actions"><slot name="actions" /></div>
  </div>
</template>

<style scoped>
.error-notice {
  display: flex; align-items: flex-start; flex-wrap: wrap; gap: var(--app-space-3); min-width: 0;
  padding: var(--app-space-3) var(--app-space-4); border-radius: var(--md-sys-shape-corner-medium);
  background: var(--md-sys-color-error-container); color: var(--md-sys-color-on-error-container);
}
.icon { flex: none; margin-block-start: var(--app-space-1); }
.content { display: flex; flex: 1 1 160px; flex-direction: column; gap: var(--app-space-1); min-width: 0; overflow-wrap: anywhere; }
.title, .message, .hint { margin: 0; white-space: pre-wrap; }
.meta { display: flex; flex-wrap: wrap; gap: var(--app-space-2); }
.actions { display: flex; align-items: center; flex-wrap: wrap; gap: var(--app-space-1); margin-inline-start: auto; max-width: 100%; }
.compact { padding: var(--app-space-2) var(--app-space-3); gap: var(--app-space-2); border-radius: var(--md-sys-shape-corner-small); }
.compact .icon { margin-block-start: 0; }
</style>
