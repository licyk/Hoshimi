<script setup lang="ts">
import { ref } from 'vue';
import Chip from '@/ui/Chip.vue';
import TextField from '@/ui/TextField.vue';

/**
 * A list of short values (addresses, host names, origins) as chips: type one and press Enter (or
 * paste several separated by spaces or commas) to add it; remove one with its ×. Each change is
 * emitted whole as ``change``, so a setting saves as soon as it changes, like every other field.
 */
const props = withDefaults(defineProps<{ label: string; values: string[]; supportingText?: string; placeholder?: string; removeLabel?: (value: string) => string }>(), {
  supportingText: '',
  placeholder: '',
  removeLabel: (value: string) => `Remove ${value}`,
});
const emit = defineEmits<{ change: [values: string[]] }>();
const draft = ref('');

function commit() {
  const added = draft.value.split(/[\s,]+/).map((v) => v.trim()).filter(Boolean);
  draft.value = '';
  const next = [...props.values];
  for (const value of added) if (!next.includes(value)) next.push(value);
  if (next.length !== props.values.length) emit('change', next);
}
const remove = (value: string) => emit('change', props.values.filter((v) => v !== value));
</script>

<template>
  <div class="chip-input">
    <TextField v-model="draft" :label="label" :placeholder="placeholder" :supporting-text="supportingText" @enter="commit" @change="commit" />
    <ul v-if="values.length" class="chips" :aria-label="label">
      <li v-for="v in values" :key="v"><Chip :label="v" removable :title="removeLabel(v)" @remove="remove(v)" /></li>
    </ul>
  </div>
</template>

<style scoped>
.chip-input { display: flex; flex-direction: column; gap: var(--app-space-2); min-width: 0; }
.chips { display: flex; flex-wrap: wrap; gap: var(--app-space-2); margin: 0; padding: 0; list-style: none; }
.chips li { min-width: 0; max-width: 100%; }
.chips :deep(.label) { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }
</style>
