<script setup lang="ts">
import { TRANSITIONS } from '@/ui/motion/transitions';

/**
 * One keyed child (the slot's ``v-if`` or ``:key``) replaced by another with a fade through: the
 * old fades out quickly and the new fades in, slightly scaled, as it does between destinations.
 * Both share one grid cell while they cross, so the box takes the taller of the two instead of
 * stacking them or collapsing to nothing in between: a list that a new filter replaces.
 */
</script>

<template>
  <div class="fade-swap">
    <Transition :name="TRANSITIONS.fadeThrough">
      <slot />
    </Transition>
  </div>
</template>

<style scoped>
.fade-swap { display: grid; min-width: 0; }
.fade-swap > :deep(*) { grid-area: 1 / 1; min-width: 0; }
</style>
