<script setup lang="ts">
import { computed, ref } from 'vue';
import AxisPanes from '@/ui/AxisPanes.vue';
import { useElementWidth } from '@/ui/useElementWidth';

/**
 * A list of items and the chosen item's detail (Material's list-detail canonical layout). With
 * room for both (``WIDE`` px of this pane, not of the window: the rail and a side sheet take their
 * share) they sit side by side, each scrolling on its own, and the list keeps its place while
 * items are opened. With less, one shows at a time and moving between them slides along the
 * shared axis, deeper to the right. ``detail`` says whether an item is chosen; the page owns that
 * (a route parameter), so the back gesture and a reload keep it.
 */
const props = defineProps<{ detail: boolean }>();
const WIDE = 840;
const root = ref<HTMLElement | null>(null);
const width = useElementWidth(root, WIDE);
const wide = computed(() => width.value >= WIDE);
const pane = computed(() => (props.detail ? 'detail' : 'list'));
const ORDER = ['list', 'detail'] as const;
</script>

<template>
  <div ref="root" class="list-detail" :class="{ wide, detail }">
    <template v-if="wide">
      <section class="pane list-pane"><slot name="list" :wide="true" /></section>
      <section class="pane detail-pane">
        <slot v-if="detail" name="detail" :wide="true" />
        <slot v-else name="placeholder" />
      </section>
    </template>
    <AxisPanes v-else :value="pane" :order="ORDER">
      <section v-if="pane === 'list'" key="list" class="pane"><slot name="list" :wide="false" /></section>
      <section v-else key="detail" class="pane"><slot name="detail" :wide="false" /></section>
    </AxisPanes>
  </div>
</template>

<style scoped>
.list-detail { min-width: 0; }
.list-detail.wide { display: grid; grid-template-columns: var(--app-width-pane) minmax(0, 1fr); gap: var(--app-space-4); height: 100%; min-height: 0; }
.pane { min-width: 0; }
.wide .pane { min-height: 0; overflow: auto; scrollbar-width: thin; overscroll-behavior: contain; }
/* The list pane scrolls under its own sticky search; the detail is the page beside it. */
.wide .list-pane { display: flex; flex-direction: column; }
</style>
