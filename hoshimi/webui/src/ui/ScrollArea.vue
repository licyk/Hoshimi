<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from 'vue';

/**
 * A list that can grow without bound (accounts, calls, models) scrolls inside itself instead of
 * stretching the page: it grows to ``max-height`` (``--app-height-list`` by default) and then
 * scrolls, with its ``header`` slot held at the top and a soft fade at an edge that has more
 * beyond it. ``end`` fires once when the bottom comes within a row's reach, for paging in more.
 */
withDefaults(defineProps<{ maxHeight?: string; label?: string }>(), { maxHeight: 'var(--app-height-list)', label: undefined });
const emit = defineEmits<{ end: [] }>();
const box = ref<HTMLElement | null>(null);
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
    class="scroll-area"
    :class="{ above, below }"
    :style="{ maxHeight }"
    :role="label ? 'region' : undefined"
    :aria-label="label"
    :tabindex="label ? 0 : undefined"
    @scroll.passive="update"
  >
    <div v-if="$slots.header" class="head"><slot name="header" /></div>
    <div class="body"><slot /></div>
  </div>
</template>

<style scoped>
.scroll-area {
  position: relative; overflow: auto; min-height: 0; min-width: 0; overscroll-behavior: contain;
  scrollbar-width: thin; scrollbar-gutter: stable;
  --fade: var(--app-space-6);
}
/* The edges with more beyond them fade out; the sticky header stays solid above the top fade. */
.scroll-area.below { mask-image: linear-gradient(to bottom, black calc(100% - var(--fade)), transparent); }
/* Solid in the tone of the surface it sits on (Surface sets --app-surface-bg), so rows pass under it. */
.head { position: sticky; top: 0; z-index: var(--app-z-sticky); background: var(--app-surface-bg, var(--md-sys-color-surface)); }
.scroll-area:focus-visible { outline-offset: -2px; }
</style>
