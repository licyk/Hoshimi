<script setup lang="ts">
import { computed, nextTick, onMounted, ref, watch } from 'vue';
import AppIcon from '@/ui/AppIcon.vue';
import { ArrowUp, Square } from '@/ui/icons';
import { TRANSITIONS } from '@/ui/motion/transitions';

/**
 * Where a message is written and sent, as in a coding agent's chat: one rounded surface holding the
 * text, a row of tools under it (``tools`` at the start, ``actions`` before the send button) and a
 * round send button that becomes Stop while an answer comes in. The text grows with what is typed
 * up to a few lines, then scrolls. Enter sends and Shift+Enter starts a new line; Enter while an
 * input method composes is the method's own.
 */
const props = withDefaults(defineProps<{
  /** The text's accessible name. */
  label: string;
  placeholder?: string;
  sendLabel: string;
  stopLabel: string;
  /** An answer is coming in: the button stops it. */
  busy?: boolean;
  /** Whether a message can go out now, apart from having text (a model is chosen). */
  canSend?: boolean;
}>(), { canSend: true });
const emit = defineEmits<{ send: []; stop: [] }>();
const text = defineModel<string>({ default: '' });

const input = ref<HTMLTextAreaElement | null>(null);
const ready = computed(() => !props.busy && props.canSend && !!text.value.trim());

/** Fit the box to the text; its max-height takes over from there and it scrolls. */
function fit() {
  const el = input.value;
  if (!el) return;
  el.style.height = 'auto';
  el.style.height = `${el.scrollHeight}px`;
}
watch(text, () => nextTick(fit));
onMounted(fit);

function submit() {
  if (ready.value) emit('send');
}
function onKeydown(e: KeyboardEvent) {
  if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) {
    e.preventDefault();
    submit();
  }
}
/** A press on the surface around the text (not on a control) puts the caret in it. */
function onSurfaceDown(e: PointerEvent) {
  if ((e.target as Element).closest('button, a, input, textarea, [role="dialog"]')) return;
  e.preventDefault();
  input.value?.focus();
}

defineExpose({ focus: () => input.value?.focus() });
</script>

<template>
  <div class="composer">
    <div class="surface" @pointerdown="onSurfaceDown">
      <textarea
        ref="input"
        v-model="text"
        class="input app-scroll app-scroll-auto type-body-large"
        rows="1"
        :placeholder="placeholder"
        :aria-label="label"
        @keydown="onKeydown"
      />
      <div class="bar">
        <div class="tools"><slot name="tools" /></div>
        <div class="actions">
          <slot name="actions" />
          <button
            type="button"
            class="send"
            :class="{ ready, busy }"
            :disabled="!busy && !ready"
            :aria-label="busy ? stopLabel : sendLabel"
            :title="busy ? stopLabel : sendLabel"
            @click="busy ? emit('stop') : submit()"
          >
            <Transition :name="TRANSITIONS.iconSwap">
              <AppIcon v-if="busy" key="stop" :icon="Square" :size="18" class="glyph stop" />
              <AppIcon v-else key="send" :icon="ArrowUp" :size="20" class="glyph" />
            </Transition>
          </button>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
/* The surface draws the box and eases its own focus; the root is left to a transition that moves it. */
.composer { min-width: 0; }
.surface {
  display: flex; flex-direction: column; min-width: 0; cursor: text;
  background: var(--md-sys-color-surface-container-lowest); border: 1px solid var(--md-sys-color-outline-variant);
  border-radius: var(--md-sys-shape-corner-extra-large); box-shadow: var(--app-elevation-1);
  transition:
    border-color var(--md-sys-motion-duration-short4) var(--md-sys-motion-easing-standard),
    box-shadow var(--md-sys-motion-duration-medium1) var(--md-sys-motion-easing-standard);
}
.surface:focus-within { border-color: color-mix(in srgb, var(--md-sys-color-primary) 45%, var(--md-sys-color-outline-variant)); box-shadow: var(--app-elevation-2); }
.input {
  display: block; width: 100%; min-height: 56px; max-height: min(40vh, 240px); margin: 0; padding: var(--app-space-4) var(--app-space-5) var(--app-space-1);
  border: 0; outline: none; background: transparent; color: var(--md-sys-color-on-surface); font: inherit; resize: none;
}
.input::placeholder { color: var(--md-sys-color-on-surface-variant); opacity: 0.8; }
.bar { display: flex; align-items: center; gap: var(--app-space-2); padding: var(--app-space-1) var(--app-space-2) var(--app-space-2) var(--app-space-3); min-width: 0; }
.tools { display: flex; align-items: center; gap: 2px; flex: 1; min-width: 0; }
.actions { position: relative; display: flex; align-items: center; gap: var(--app-space-1); flex: none; }
.send {
  position: relative; display: grid; place-items: center; flex: none; width: 36px; height: 36px; padding: 0;
  border: 0; border-radius: var(--md-sys-shape-corner-full); cursor: pointer;
  background: var(--md-sys-color-surface-container-highest); color: var(--md-sys-color-on-surface-variant);
  transition:
    background-color var(--md-sys-motion-duration-medium1) var(--md-sys-motion-easing-standard),
    color var(--md-sys-motion-duration-medium1) var(--md-sys-motion-easing-standard),
    transform var(--md-sys-motion-duration-short3) var(--md-sys-motion-easing-standard),
    box-shadow var(--md-sys-motion-duration-short4) var(--md-sys-motion-easing-standard);
}
.send:disabled { cursor: default; color: color-mix(in srgb, var(--md-sys-color-on-surface) 38%, transparent); }
.send.ready { background: var(--md-sys-color-primary); color: var(--md-sys-color-on-primary); }
.send.busy { background: var(--md-sys-color-inverse-surface); color: var(--md-sys-color-inverse-on-surface); }
.send:not(:disabled):hover { box-shadow: var(--app-elevation-1); }
.send:not(:disabled):active { transform: scale(0.9); }
/* The two glyphs share the button's one cell while they cross. */
.glyph { grid-area: 1 / 1; }
.glyph.stop { fill: currentColor; }
/* While an answer comes in, an arc runs round the button. */
.send.busy::after {
  content: ''; position: absolute; inset: -4px; border-radius: inherit; pointer-events: none;
  border: 2px solid transparent; border-top-color: var(--md-sys-color-primary);
  animation: composer-busy 1.1s linear infinite;
}
@keyframes composer-busy { to { transform: rotate(360deg); } }
@media (max-width: 599px) {
  .input { padding-inline: var(--app-space-4); }
}
</style>
