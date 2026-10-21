<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue';
import { useElementHeight } from '@/ui/useElementHeight';

/**
 * A list that can grow without bound (accounts, calls, models) scrolls inside itself instead of
 * stretching the page: it grows to ``max-height`` (``--app-height-list`` by default) and then
 * scrolls, with its ``header`` slot held at the top and a soft fade at an edge that has more
 * beyond it. The scrollbar keeps a lane of its own (``.app-scroll``) whose track starts below the
 * header, and the fade covers the rows only, never the scrollbar. ``end`` fires once when the bottom comes within a row's reach, for paging in more.
 */
withDefaults(defineProps<{ maxHeight?: string; label?: string }>(), { maxHeight: 'var(--app-height-list)', label: undefined });
const emit = defineEmits<{ end: [] }>();
const box = ref<HTMLElement | null>(null);
const head = ref<HTMLElement | null>(null);
const headHeight = useElementHeight(head);
const headStyle = computed(() => (headHeight.value ? { '--app-scroll-head': `${headHeight.value}px` } : {}));
const above = ref(false);
const below = ref(false);
const END_MARGIN = 120;
let reachedEnd = false;

function update() {
  const el = box.value;
  if (!el) return;
  above.value = el.scrollTop > 1;
  const rest = el.scrollHeight - el.clientHeight - el.scrollTop;
  below.value = rest > 1;
  // Fire once per approach: scrolling back up and down again asks again.
  if (rest < END_MARGIN) {
    if (!reachedEnd) emit('end');
    reachedEnd = true;
  } else reachedEnd = false;
}

let observer: ResizeObserver | null = null;
onMounted(() => {
  update();
  if (typeof ResizeObserver !== 'undefined' && box.value) {
    observer = new ResizeObserver(update);
    observer.observe(box.value);
    for (const child of Array.from(box.value.children)) observer.observe(child);
  }
});
onBeforeUnmount(() => observer?.disconnect());
defineExpose({ box, update });
</script>

<template>
  <div
    ref="box"
    class="scroll-area app-scroll"
    :class="{ above, below }"
    :style="{ maxHeight, ...headStyle }"
    :role="label ? 'region' : undefined"
    :aria-label="label"
    :tabindex="label ? 0 : undefined"
    @scroll.passive="update"
  >
    <div v-if="$slots.header" ref="head" class="head"><slot name="header" /></div>
    <div class="body"><slot /></div>
  </div>
</template>

<style scoped>
.scroll-area { position: relative; min-height: 0; min-width: 0; --fade: var(--app-space-6); }
/* The bottom edge, with more beyond it, fades into the surface: a band held at the foot of the
   view (sticky, after the rows), as wide as the content box, so the scrollbar's lane beside it
   stays clear. It takes no room of its own (the negative margin). */
.scroll-area::after {
  content: ''; position: sticky; left: 0; bottom: 0; z-index: var(--app-z-sticky); display: block; height: var(--fade); margin-top: calc(-1 * var(--fade));
  background: linear-gradient(to bottom, transparent, var(--app-surface-bg, var(--md-sys-color-surface)));
  pointer-events: none; opacity: 0; transition: opacity var(--md-sys-motion-duration-short4) var(--md-sys-motion-easing-standard);
}
.scroll-area.below::after { opacity: 1; }
/* Solid in the tone of the surface it sits on (Surface sets --app-surface-bg), so rows pass under it. */
.head { position: sticky; top: 0; z-index: var(--app-z-sticky); background: var(--app-surface-bg, var(--md-sys-color-surface)); }
.scroll-area:focus-visible { outline-offset: -2px; }
</style>
