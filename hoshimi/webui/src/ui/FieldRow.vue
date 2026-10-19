<script setup lang="ts">
/** Keep actions centred on the field box while supporting text flows below it. */
withDefaults(defineProps<{ grow?: boolean }>(), { grow: true });
</script>

<template>
  <div class="field-row">
    <div class="field" :class="{ grow }"><slot /></div>
    <div v-if="$slots.end" class="end"><slot name="end" /></div>
  </div>
</template>

<style scoped>
.field-row { display: flex; flex-wrap: wrap; align-items: flex-start; gap: var(--app-space-2); min-width: 0; }
.field { display: flex; flex-direction: column; min-width: 0; }
.field.grow { flex: 1 1 240px; }
.end { display: flex; flex-wrap: wrap; align-items: center; gap: var(--app-space-2); min-height: var(--app-field-height); }
</style>
