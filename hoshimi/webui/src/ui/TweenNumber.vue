<script setup lang="ts">
import { computed } from 'vue';
import { useTween } from '@/ui/motion/useTween';

/**
 * A number in running text or a table cell that glides to each new value (a range changed, a call
 * finished) instead of jumping; ``format`` writes the tweened number. Null shows a dash.
 */
const props = withDefaults(defineProps<{ value: number | null | undefined; format?: (n: number) => string }>(), { format: (n: number) => Math.round(n).toLocaleString() });
const shown = useTween(() => props.value);
const text = computed(() => (shown.value === null || shown.value === undefined ? '—' : props.format(shown.value)));
</script>

<template>
  <span class="num">{{ text }}</span>
</template>
