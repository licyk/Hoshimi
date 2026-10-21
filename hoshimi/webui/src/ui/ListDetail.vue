<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue';
import AxisPanes from '@/ui/AxisPanes.vue';
import { TRANSITIONS } from '@/ui/motion/transitions';
import { useElementWidth } from '@/ui/useElementWidth';

/**
 * A list of items and the chosen item's detail (Material's list-detail canonical layout). With
 * room for both (``WIDE`` px of this pane, not of the window: the rail and a side sheet take their
 * share) they sit side by side, each scrolling on its own, and the list keeps its place while
 * items are opened. With less, one shows at a time and moving between them slides along the
 * shared axis, deeper to the right. ``detail`` says whether an item is chosen; the page owns that
 * (a route parameter), so the back gesture and a reload keep it.
 *
 * Side by side, choosing another item moves the detail along the list's axis: an item further down
 * rises from below, one above comes down from above (``detailKey`` names the item, ``order`` is
 * the list as shown). Between nothing chosen and an item, or to one the list does not show, the
 * pane fades through. Each item's page starts at its top.
 */
const props = withDefaults(defineProps<{ detail: boolean; detailKey?: string | null; order?: readonly string[] }>(), { detailKey: null, order: () => [] });
const WIDE = 840;
const root = ref<HTMLElement | null>(null);
const detailPane = ref<HTMLElement | null>(null);
const width = useElementWidth(root, WIDE);
const wide = computed(() => width.value >= WIDE);
const pane = computed(() => (props.detail ? 'detail' : 'list'));
const ORDER = ['list', 'detail'] as const;

const shownKey = computed(() => (props.detail ? (props.detailKey ?? 'detail') : ''));
const name = ref<string>(TRANSITIONS.fadeThrough);
const dir = ref(1);
// Decided before the pane re-renders (sync), so the leaving page takes the new transition's classes.
let leavingScroll = 0;
watch(
  shownKey,
  (next, prev) => {
    const from = prev ? props.order.indexOf(prev) : -1;
    const to = next ? props.order.indexOf(next) : -1;
    if (from >= 0 && to >= 0 && from !== to) {
      name.value = TRANSITIONS.sharedAxisY;
      dir.value = to > from ? 1 : -1;
    } else name.value = TRANSITIONS.fadeThrough;
    leavingScroll = detailPane.value?.scrollTop ?? 0;
  },
  { flush: 'sync' },
);
// The leaving page is taken out of the flow where it stood on screen, then the pane goes to the top
// for the page that enters.
function onBeforeLeave(el: Element) {
  const e = el as HTMLElement;
  Object.assign(e.style, { position: 'absolute', insetInline: '0', top: `${e.offsetTop - leavingScroll}px`, width: `${e.offsetWidth}px` });
}
watch(shownKey, () => void nextTick(() => detailPane.value?.scrollTo({ top: 0 })));
</script>

<template>
  <div ref="root" class="list-detail" :class="{ wide, detail }">
    <template v-if="wide">
      <section class="pane list-pane app-scroll"><slot name="list" :wide="true" /></section>
      <section ref="detailPane" class="pane detail-pane app-scroll" :style="{ '--axis-dir': dir }">
        <Transition :name="name" @before-leave="onBeforeLeave">
          <div v-if="detail" :key="shownKey" class="detail-page"><slot name="detail" :wide="true" /></div>
          <div v-else key="" class="detail-page"><slot name="placeholder" /></div>
        </Transition>
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
.list-detail.wide { display: grid; grid-template-columns: var(--app-width-pane) minmax(0, 1fr); gap: var(--app-space-3); height: 100%; min-height: 0; }
.pane { min-width: 0; }
.wide .pane { min-height: 0; }
/* The list pane scrolls under its own sticky search; the detail is the page beside it. Rows stop
   a step short of the scrollbar's lane rather than running into it. */
.wide .list-pane { display: flex; flex-direction: column; padding-inline-end: var(--app-space-1); }
.detail-pane { position: relative; overflow-x: clip; }
.detail-page { min-width: 0; }
</style>
