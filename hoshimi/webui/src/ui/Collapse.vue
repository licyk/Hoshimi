<script setup lang="ts">
import { sectionHooks, TRANSITIONS } from '@/ui/motion/transitions';

/**
 * A block of a page that comes and goes: a section shown only while something is true (calls in
 * progress), a banner. It opens to its height and closes again, the gap beside it with it, so the
 * blocks below slide instead of jumping; its content fades in as it opens and out before it closes.
 * The wrapper has no padding of its own, so it closes to nothing whatever it holds.
 */
defineProps<{ show: boolean }>();
</script>

<template>
  <Transition :name="TRANSITIONS.section" v-bind="sectionHooks">
    <div v-if="show" class="collapse"><slot /></div>
  </Transition>
</template>

<style scoped>
.collapse { min-width: 0; }
</style>
