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
 * input method composes is the method's own. Escape stops an answer coming in, and the up arrow in
 * an empty box asks for the last message back (``recall``), to edit it. What is attached goes in
 * ``above`` (over the text); ``attached`` lets a message go with no text of its own.
 *
 * With ``steerable``, an answer coming in can be written to: while there is text, the button (and
 * Enter) sends it to the answer under way (``steer``) instead of stopping it; Escape still stops.
 *
 * A menu that reads the text as it is typed (slash commands) takes the keys first through
 * ``keydownGuard`` (true: it dealt with the key), and names itself with ``combobox``, so the text box
 * is announced as the combobox that controls it.
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
  /** Something is attached: the message can go without text. */
  attached?: boolean;
  /** The answer coming in takes messages while it runs. */
  steerable?: boolean;
  steerLabel?: string;
  keydownGuard?: (e: KeyboardEvent) => boolean;
  combobox?: { expanded: boolean; controls: string; active?: string } | null;
}>(), { canSend: true, attached: false, steerable: false, steerLabel: '', keydownGuard: undefined, combobox: null });
const emit = defineEmits<{ send: []; stop: []; recall: []; steer: [] }>();
const text = defineModel<string>({ default: '' });

const input = ref<HTMLTextAreaElement | null>(null);
const ready = computed(() => !props.busy && props.canSend && (!!text.value.trim() || props.attached));
/** Busy, with something to say to the answer under way: the button sends it rather than stopping. */
const steering = computed(() => props.busy && props.steerable && !!text.value.trim());
const stopping = computed(() => props.busy && !steering.value);

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
  if (steering.value) emit('steer');
  else if (ready.value) emit('send');
}
function onKeydown(e: KeyboardEvent) {
  if (props.keydownGuard?.(e)) return;
  if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) {
    e.preventDefault();
    submit();
  } else if (e.key === 'Escape' && props.busy) {
    e.preventDefault();
    emit('stop');
  } else if (e.key === 'ArrowUp' && !text.value && !e.isComposing) {
    e.preventDefault();
    emit('recall');
  }
}
/** A press on the surface around the text (not on a control) puts the caret in it. */
function onSurfaceDown(e: PointerEvent) {
  if ((e.target as Element).closest('button, a, input, textarea, [role="dialog"]')) return;
  e.preventDefault();
  input.value?.focus();
}

defineExpose({ focus: () => input.value?.focus(), surface: () => input.value?.closest<HTMLElement>('.surface') ?? null });
</script>

<template>
  <div class="composer">
    <div class="surface" @pointerdown="onSurfaceDown">
      <slot name="above" />
      <textarea
        ref="input"
        v-model="text"
        class="input app-scroll app-scroll-auto type-body-large"
        rows="1"
        :placeholder="placeholder"
        :aria-label="label"
        :role="combobox ? 'combobox' : undefined"
        :aria-expanded="combobox ? combobox.expanded : undefined"
        :aria-controls="combobox?.expanded ? combobox.controls : undefined"
        :aria-activedescendant="combobox?.expanded ? combobox.active : undefined"
        :aria-autocomplete="combobox ? 'list' : undefined"
        @keydown="onKeydown"
      />
      <div class="bar">
        <div class="tools"><slot name="tools" /></div>
        <div class="actions">
          <slot name="actions" />
          <button
            type="button"
            class="send"
            :class="{ ready: ready || steering, busy: stopping }"
            :disabled="!busy && !ready"
            :aria-label="steering ? steerLabel : stopping ? stopLabel : sendLabel"
            :title="steering ? steerLabel : stopping ? stopLabel : sendLabel"
            @click="stopping ? emit('stop') : submit()"
          >
            <Transition :name="TRANSITIONS.iconSwap">
              <AppIcon v-if="stopping" key="stop" :icon="Square" :size="18" class="glyph stop" />
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
