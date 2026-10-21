<script setup lang="ts">
import { computed } from 'vue';
import type { LiveCall } from '@/api/types';
import { compact, duration } from '@/format';
import { useI18n } from '@/i18n';
import { useLiveStore } from '@/stores/live';
import { AppIcon, icons } from '@/ui';

/**
 * One call running through the gateway: the model, where it went (provider and account, once the
 * station has answered), the phase it is in on a three-step track, and its numbers so far: time
 * running, time to first token, tokens out (estimated from the streamed text, hence "≈", until the
 * station's own count arrives with the finish) and the live rate.
 */
const props = defineProps<{ call: LiveCall }>();
const { t } = useI18n();
const live = useLiveStore();
const PHASES = ['waiting', 'reasoning', 'answering'] as const;
const PHASE_ICONS = { waiting: icons.Hourglass, reasoning: icons.Brain, answering: icons.MessagesSquare, tool: icons.Wrench } as const;
const reached = computed(() => {
  // A tool call is answering in its own way; a call that never reasoned skips that step.
  const phase = props.call.phase === 'tool' ? 'answering' : props.call.phase;
  return PHASES.indexOf(phase as (typeof PHASES)[number]);
});
const route = computed(() => [props.call.provider, props.call.account].filter(Boolean).join(' / ') || t('live.routing'));
const icon = computed(() => PHASE_ICONS[props.call.phase as keyof typeof PHASE_ICONS] ?? icons.Hourglass);
</script>

<template>
  <article class="live-call" :aria-label="call.name || call.model">
    <header class="head">
      <AppIcon :icon="icon" :size="20" class="phase-icon" :class="call.phase" />
      <div class="titles">
        <span class="type-title-small name" :title="call.model">{{ call.name || call.model }}</span>
        <span class="type-body-small muted sub">
          {{ route }} · {{ t(`usage.origins.${call.origin}`) }}<template v-if="call.effort"> · {{ call.effort }}</template><template v-if="call.attempts > 1"> · {{ t('live.attempts', { n: call.attempts }) }}</template>
        </span>
      </div>
      <span class="type-label-large num elapsed">{{ duration(live.elapsed(call)) }}</span>
    </header>
    <ol class="track" :aria-label="t(`live.phases.${call.phase}`)">
      <li v-for="(p, i) in PHASES" :key="p" class="step" :class="{ done: i < reached, current: i === reached }">
        <span class="bar" />
        <span class="type-label-small label">{{ t(`live.phases.${p}`) }}</span>
      </li>
    </ol>
    <dl class="numbers type-body-small">
      <div><dt class="muted">{{ t('usage.ttft') }}</dt><dd class="num">{{ duration(call.ttft_ms) }}</dd></div>
      <div><dt class="muted">{{ t('live.tokensOut') }}</dt><dd class="num">≈ {{ compact(call.est_output_tokens) }}</dd></div>
      <div><dt class="muted">{{ t('usage.tps') }}</dt><dd class="num">{{ call.tps ?? '—' }}</dd></div>
    </dl>
  </article>
</template>

<style scoped>
.live-call { display: flex; flex-direction: column; gap: var(--app-space-2); padding: var(--app-space-3) var(--app-space-4); border-radius: var(--md-sys-shape-corner-medium); background: var(--md-sys-color-surface-container); min-width: 0; }
.head { display: flex; align-items: center; gap: var(--app-space-3); min-width: 0; }
.phase-icon { color: var(--md-sys-color-on-surface-variant); }
.phase-icon.reasoning, .phase-icon.answering, .phase-icon.tool { color: var(--md-sys-color-primary); }
.titles { display: flex; flex-direction: column; flex: 1; min-width: 0; }
.name, .sub { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.elapsed { white-space: nowrap; }
.track { display: grid; grid-template-columns: repeat(3, 1fr); gap: var(--app-space-1); margin: 0; padding: 0; list-style: none; }
.step { display: flex; flex-direction: column; gap: 2px; min-width: 0; color: var(--md-sys-color-on-surface-variant); }
.bar { height: 4px; border-radius: var(--md-sys-shape-corner-full); background: var(--md-sys-color-surface-container-highest); transition: background-color var(--md-sys-motion-duration-medium1) var(--md-sys-motion-easing-standard); }
.done .bar { background: var(--md-sys-color-primary); }
.current .bar { background: var(--md-sys-color-primary); animation: live-current 1.4s var(--md-sys-motion-easing-standard) infinite; }
.current { color: var(--md-sys-color-on-surface); }
.label { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.numbers { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: var(--app-space-2); margin: 0; }
.numbers div { display: flex; flex-direction: column; min-width: 0; }
.numbers dd { margin: 0; font-weight: 500; }
@keyframes live-current { 50% { opacity: 0.45; } }
</style>
