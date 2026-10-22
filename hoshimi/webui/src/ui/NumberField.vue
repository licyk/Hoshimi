<script setup lang="ts">
import '@material/web/textfield/outlined-text-field.js';
import { computed, onBeforeUnmount, ref, watch } from 'vue';
import AppIcon from '@/ui/AppIcon.vue';
import { ChevronDown, ChevronUp } from '@/ui/icons';

/**
 * A number field with arrows that step it by ``step``, within ``min``/``max``. A step shows at once
 * and is reported (``change``) once the arrows pause for ``commitDelay``, so a run of clicks makes
 * one save rather than one per click; typing is reported on the field's own change, as TextField's.
 * The arrows stay out of the tab order: ↑/↓ already step the focused input. A step lands on a
 * multiple of ``step`` (16 MiB up by 1 MiB is 17 MiB, 1000 bytes up is 1 MiB), then on the bounds.
 */
const props = withDefaults(
  defineProps<{
    modelValue?: number | null;
    label?: string;
    supportingText?: string;
    disabled?: boolean;
    min?: number;
    max?: number;
    step?: number;
    increaseLabel: string;
    decreaseLabel: string;
    commitDelay?: number;
  }>(),
  { modelValue: null, label: '', supportingText: '', min: undefined, max: undefined, step: 1, commitDelay: 600 },
);
const emit = defineEmits<{ change: [string] }>();

const text = (value: number | null) => (value === null || value === undefined ? '' : String(value));
const shown = ref(text(props.modelValue));
let timer: ReturnType<typeof setTimeout> | undefined;
// A step waiting to be reported is newer than the saved value, so it is not overwritten meanwhile.
watch(() => props.modelValue, (value) => {
  if (timer === undefined) shown.value = text(value);
});

const value = computed(() => (shown.value.trim() === '' ? null : Number(shown.value)));
const canIncrease = computed(() => !props.disabled && (props.max === undefined || value.value === null || !(value.value >= props.max)));
const canDecrease = computed(() => !props.disabled && (props.min === undefined || value.value === null || !(value.value <= props.min)));

function stepBy(direction: 1 | -1) {
  const current = value.value !== null && Number.isFinite(value.value) ? value.value : null;
  let next: number;
  if (current === null) next = props.min ?? 0;
  else if (direction > 0) next = Math.floor(current / props.step) * props.step + props.step;
  else next = Math.ceil(current / props.step) * props.step - props.step;
  next = Math.min(props.max ?? Infinity, Math.max(props.min ?? -Infinity, next));
  shown.value = String(next);
  clearTimeout(timer);
  timer = setTimeout(commit, props.commitDelay);
}

function commit() {
  clearTimeout(timer);
  timer = undefined;
  emit('change', shown.value);
}
// Leaving the field, or the page, reports a step still waiting rather than dropping it.
const flush = () => timer !== undefined && commit();
onBeforeUnmount(flush);

function onChange(event: Event) {
  clearTimeout(timer);
  timer = undefined;
  emit('change', (event.target as HTMLInputElement).value);
}
</script>

<template>
  <md-outlined-text-field
    class="number-field"
    type="number"
    no-spinner
    :label="label"
    :value.prop="shown"
    :supporting-text="supportingText"
    :disabled.prop="disabled"
    :min="min"
    :max="max"
    :step="step"
    @input="shown = ($event.target as HTMLInputElement).value"
    @change="onChange"
    @focusout="flush"
  >
    <span slot="trailing-icon" class="arrows">
      <!-- mousedown is held back so the input keeps focus (and its ↑/↓) while the arrows are used. -->
      <button type="button" tabindex="-1" class="arrow state-layer" :aria-label="increaseLabel" :disabled="!canIncrease" @mousedown.prevent @click="stepBy(1)">
        <AppIcon :icon="ChevronUp" :size="18" />
      </button>
      <button type="button" tabindex="-1" class="arrow state-layer" :aria-label="decreaseLabel" :disabled="!canDecrease" @mousedown.prevent @click="stepBy(-1)">
        <AppIcon :icon="ChevronDown" :size="18" />
      </button>
    </span>
  </md-outlined-text-field>
</template>

<style scoped>
.number-field {
  width: 100%;
  min-width: 0;
  /* Its own height, even in a stretching row (see TextField). */
  height: fit-content;
  /* The arrows are wider than an icon: the trailing slot makes room for them. */
  --md-outlined-text-field-trailing-icon-size: 32px;
}
.arrows { display: flex; flex-direction: column; gap: 0; }
.arrow {
  display: grid; place-items: center; width: 32px; height: 24px; padding: 0; border: 0; border-radius: var(--md-sys-shape-corner-small);
  background: transparent; color: var(--md-sys-color-on-surface-variant); cursor: pointer;
}
.arrow:disabled { opacity: var(--app-disabled-content-opacity); cursor: default; }
.arrow:disabled::before { display: none; }
</style>
