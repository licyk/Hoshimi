<script setup lang="ts">
import { computed } from 'vue';
import { elapsed } from '@/format';
import { useI18n } from '@/i18n';
import { useElapsed } from '@/ui/useElapsed';

/**
 * The line at the foot of a reply under way: a pulsing dot, what it is doing now (a light sweeps
 * across the words), and how long it has worked so far, ticking each second. While it waits for
 * the user's approval the clock stops and the line dims: the time is the reply's, not the user's.
 * ``hint`` says how to stop it.
 */
const props = defineProps<{ label: string; startedAt: number; paused?: boolean; hint?: string }>();
const { t } = useI18n();
const ms = useElapsed(() => props.startedAt, () => true, () => !!props.paused);
const time = computed(() => elapsed(ms.value));
</script>

<template>
  <div class="run-status type-body-medium" :class="{ paused }" role="status" :aria-label="`${label} ${t('playground.elapsedFor', { time })}`">
    <span class="dot" aria-hidden="true" />
    <span class="label">{{ label }}</span>
    <span class="time muted" aria-hidden="true">{{ time }}</span>
    <span v-if="hint" class="hint type-body-small muted" aria-hidden="true">· {{ hint }}</span>
  </div>
</template>

<style scoped>
.run-status { display: flex; align-items: center; gap: var(--app-space-2); min-height: 28px; min-width: 0; color: var(--md-sys-color-on-surface-variant); }
.dot { flex: none; width: 10px; height: 10px; border-radius: 50%; background: var(--md-sys-color-primary); animation: run-pulse 1.2s var(--md-sys-motion-easing-standard) infinite; }
@keyframes run-pulse { 0%, 100% { opacity: 0.35; transform: scale(0.8); } 50% { opacity: 1; transform: scale(1); } }
/* A light sweeps across the words while it works. */
.label {
  min-width: 0; overflow: hidden; white-space: nowrap; text-overflow: ellipsis;
  background: linear-gradient(90deg, var(--md-sys-color-on-surface-variant) 0%, var(--md-sys-color-on-surface-variant) 40%, var(--md-sys-color-primary) 50%, var(--md-sys-color-on-surface-variant) 60%, var(--md-sys-color-on-surface-variant) 100%);
  background-size: 250% 100%; -webkit-background-clip: text; background-clip: text; color: transparent;
  animation: run-shimmer 2.2s linear infinite;
}
@keyframes run-shimmer { from { background-position: 100% 0; } to { background-position: -150% 0; } }
.time { flex: none; font-variant-numeric: tabular-nums; }
.hint { flex: none; }
.paused .dot { animation: none; opacity: 0.5; background: var(--md-sys-color-tertiary); }
.paused .label { animation: none; color: var(--md-sys-color-on-surface-variant); background: none; }
.paused .time { opacity: 0.6; }
@media (max-width: 599px) { .hint { display: none; } }
@media (prefers-reduced-motion: reduce) {
  .dot { animation: none; }
  .label { animation: none; color: var(--md-sys-color-on-surface-variant); background: none; }
}
</style>
