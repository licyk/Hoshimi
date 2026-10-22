<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue';
import { duration } from '@/format';
import { useI18n } from '@/i18n';
import { AppIcon, Collapse, icons } from '@/ui';
// Not through '@/ui': markdown-it and highlight.js load with the Playground alone.
import Markdown from '@/ui/Markdown.vue';

/**
 * What a reply reasoned before it answered, folded away under one line: "Thinking…" while it
 * reasons (with the time so far), "Thought for 4.2 s" once it has answered. The reasoning opens
 * under the line on a press and stays as it was left; it starts folded. A reply with no reasoning
 * has no such line at all (the parent shows this only when there is reasoning to show).
 */
const props = defineProps<{ reasoning: string; ms: number; active: boolean }>();
const { t } = useI18n();
const open = ref(false);
const id = `reasoning-${Math.random().toString(36).slice(2, 10)}`;

// The time so far, while it reasons; the kept time once it is done.
const started = Date.now();
const now = ref(Date.now());
let timer: ReturnType<typeof setInterval> | undefined;
watch(
  () => props.active,
  (active) => {
    clearInterval(timer);
    timer = active ? setInterval(() => (now.value = Date.now()), 1000) : undefined;
  },
  { immediate: true },
);
onBeforeUnmount(() => clearInterval(timer));

const label = computed(() => {
  if (props.active) return t('playground.thinking');
  return props.ms >= 1000 ? t('playground.thoughtFor', { time: duration(props.ms) }) : t('playground.thoughtBriefly');
});
const elapsed = computed(() => (props.active && now.value - started >= 1000 ? duration(Math.floor((now.value - started) / 1000) * 1000) : ''));
</script>

<template>
  <div class="reasoning" :class="{ open, active }">
    <button type="button" class="toggle state-layer type-body-medium" :aria-expanded="open" :aria-controls="id" @click="open = !open">
      <AppIcon :icon="icons.Brain" :size="18" class="brain" />
      <span class="label">{{ label }}</span>
      <span v-if="elapsed" class="elapsed num">{{ elapsed }}</span>
      <AppIcon :icon="icons.ChevronDown" :size="18" class="chevron" />
    </button>
    <Collapse :show="open">
      <div :id="id" class="body" role="region" :aria-label="t('playground.reasoning')">
        <Markdown class="type-body-medium" :source="reasoning" :copy-label="t('playground.copyCode')" :copied-label="t('playground.codeCopied')" />
      </div>
    </Collapse>
  </div>
</template>

<style scoped>
.reasoning { display: flex; flex-direction: column; align-items: flex-start; min-width: 0; }
.toggle {
  display: inline-flex; align-items: center; gap: var(--app-space-2); height: 32px; margin-inline-start: calc(-1 * var(--app-space-2)); padding: 0 var(--app-space-2);
  border: 0; border-radius: var(--md-sys-shape-corner-full); background: transparent; color: var(--md-sys-color-on-surface-variant); cursor: pointer;
}
.elapsed { opacity: 0.8; }
.chevron { transition: transform var(--md-sys-motion-duration-short4) var(--md-sys-motion-easing-standard); }
.open .chevron { transform: rotate(180deg); }
/* While it reasons, the line breathes: something is happening that is not yet an answer. */
.active .label { animation: reasoning-pulse 1.6s var(--md-sys-motion-easing-standard) infinite; }
@keyframes reasoning-pulse { 0%, 100% { opacity: 0.55; } 50% { opacity: 1; } }
.body {
  margin: var(--app-space-1) 0 var(--app-space-2); padding: var(--app-space-1) 0 var(--app-space-1) var(--app-space-4);
  border-inline-start: 2px solid var(--md-sys-color-outline-variant); color: var(--md-sys-color-on-surface-variant);
}
@media (prefers-reduced-motion: reduce) {
  .active .label { animation: none; }
  .chevron { transition: none; }
}
</style>
