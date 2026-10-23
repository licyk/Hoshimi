<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue';
import AppIcon from '@/ui/AppIcon.vue';
import IconButton from '@/ui/IconButton.vue';
import { ChevronLeft, ChevronRight, Download, ExternalLink, ImageOff, Scan, X, ZoomIn, ZoomOut } from '@/ui/icons';
import { useLayer } from '@/ui/layers';
import { TRANSITIONS, containerFrom } from '@/ui/motion/transitions';

/**
 * Images at the size of the window, one at a time, over everything else (a modal layer: Escape
 * closes it, the page under it is inert, focus goes back to what opened it). The first grows out of
 * ``fromRect`` (the thumbnail pressed), as a dialog grows from its card; the next and previous fade
 * through. Zoom with the wheel, a pinch, a double press or + and -, and drag to look around while
 * zoomed (the image is laid out at the zoomed size rather than scaled, so an SVG, a diagram, is
 * drawn again sharp at every step); move between the images with the side buttons, the arrow keys or a swipe. Each can be
 * downloaded, or opened by itself in a new tab (not one written into the page: browsers refuse
 * those as pages).
 */
export interface ViewerImage {
  src: string;
  alt: string;
}
export interface ViewerLabels {
  title: string;
  close: string;
  previous: string;
  next: string;
  zoomIn: string;
  zoomOut: string;
  resetZoom: string;
  download: string;
  openOriginal: string;
  /** ``{n}`` of ``{total}``. */
  counter: string;
  failed: string;
}
const props = withDefaults(defineProps<{ images: ViewerImage[]; fromRect?: DOMRect | null; labels?: Partial<ViewerLabels> }>(), { fromRect: null, labels: () => ({}) });
const open = defineModel<boolean>('open', { default: false });
const index = defineModel<number>('index', { default: 0 });

const DEFAULTS: ViewerLabels = {
  title: 'Image', close: 'Close', previous: 'Previous image', next: 'Next image', zoomIn: 'Zoom in', zoomOut: 'Zoom out', resetZoom: 'Fit to window',
  download: 'Download', openOriginal: 'Open in a new tab', counter: '{n} / {total}', failed: 'This image could not be loaded.',
};
const label = computed<ViewerLabels>(() => ({ ...DEFAULTS, ...props.labels }));
const current = computed(() => props.images[index.value] ?? null);
const many = computed(() => props.images.length > 1);
const counter = computed(() => label.value.counter.replace('{n}', String(index.value + 1)).replace('{total}', String(props.images.length)));
const openable = computed(() => !!current.value && !/^data:/i.test(current.value.src));

const panel = ref<HTMLElement | null>(null);
const stage = ref<HTMLElement | null>(null);
const { zIndex } = useLayer({ kind: 'dialog', modal: true, open: () => open.value, close: () => (open.value = false), elements: () => [panel.value], initialFocus: () => panel.value });

// -- zoom and pan --------------------------------------------------------------------- #
const MIN = 1;
const MAX = 8;
const view = reactive({ scale: 1, x: 0, y: 0 });
const zoomed = computed(() => view.scale > 1.01);
const picture = ref<HTMLImageElement | null>(null);
/** The image's size fitted to the window, measured as zooming starts: zoomed, it is laid out at a multiple of it. */
const fit = reactive({ width: 0, height: 0 });
const pictureStyle = computed((): Record<string, string> => {
  if (!zoomed.value || !fit.width) return { transform: `translate(${view.x}px, ${view.y}px) scale(${view.scale})` };
  return {
    position: 'absolute', left: '50%', top: '50%', width: `${fit.width * view.scale}px`, height: `${fit.height * view.scale}px`, maxWidth: 'none', maxHeight: 'none',
    transform: `translate(calc(-50% + ${view.x}px), calc(-50% + ${view.y}px))`,
  };
});
const failed = ref(false);
function reset() {
  Object.assign(view, { scale: 1, x: 0, y: 0 });
}
/** Zoom to ``scale`` about a point of the window (the image's centre by default), which stays where it is. */
function zoomTo(scale: number, at?: { x: number; y: number }) {
  const next = Math.min(MAX, Math.max(MIN, scale));
  // Offsets ignore transforms: the fitted size, before any zoom.
  if (!zoomed.value && picture.value) Object.assign(fit, { width: picture.value.offsetWidth, height: picture.value.offsetHeight });
  const box = figure.value?.getBoundingClientRect() ?? stage.value?.getBoundingClientRect();
  const px = at && box ? at.x - (box.left + box.width / 2) : 0;
  const py = at && box ? at.y - (box.top + box.height / 2) : 0;
  const ratio = next / view.scale;
  view.x = next === 1 ? 0 : px - (px - view.x) * ratio;
  view.y = next === 1 ? 0 : py - (py - view.y) * ratio;
  view.scale = next;
}
const onWheel = (e: WheelEvent) => zoomTo(view.scale * Math.exp(-e.deltaY * 0.0015), { x: e.clientX, y: e.clientY });
const onDoubleClick = (e: MouseEvent) => (zoomed.value ? reset() : zoomTo(2.5, { x: e.clientX, y: e.clientY }));

// Pointers: one drags (zoomed) or swipes (not zoomed); two pinch.
const pointers = new Map<number, { x: number; y: number }>();
let swipe: { x: number; y: number } | null = null;
let pinch: { distance: number; scale: number } | null = null;
const distance = () => {
  const [a, b] = [...pointers.values()];
  return Math.hypot(a.x - b.x, a.y - b.y);
};
function onPointerDown(e: PointerEvent) {
  if ((e.target as Element).closest('button, a')) return;
  stage.value?.setPointerCapture?.(e.pointerId);
  pointers.set(e.pointerId, { x: e.clientX, y: e.clientY });
  swipe = pointers.size === 1 ? { x: e.clientX, y: e.clientY } : null;
  pinch = pointers.size === 2 ? { distance: distance(), scale: view.scale } : null;
}
function onPointerMove(e: PointerEvent) {
  const last = pointers.get(e.pointerId);
  if (!last) return;
  pointers.set(e.pointerId, { x: e.clientX, y: e.clientY });
  if (pinch && pointers.size === 2) {
    const [a, b] = [...pointers.values()];
    zoomTo((pinch.scale * distance()) / pinch.distance, { x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 });
  } else if (zoomed.value && pointers.size === 1) {
    view.x += e.clientX - last.x;
    view.y += e.clientY - last.y;
  }
}
function onPointerUp(e: PointerEvent) {
  if (!pointers.delete(e.pointerId)) return;
  if (pointers.size < 2) pinch = null;
  if (swipe && !zoomed.value && pointers.size === 0) {
    const dx = e.clientX - swipe.x;
    if (Math.abs(dx) > 60 && Math.abs(e.clientY - swipe.y) < 80) step(dx < 0 ? 1 : -1);
  }
  swipe = null;
}

// -- moving between images ----------------------------------------------------------------- #
/** The first image grows from its thumbnail; moving on fades through. */
const motion = ref<string>(TRANSITIONS.container);
function step(direction: 1 | -1) {
  if (!many.value) return;
  motion.value = TRANSITIONS.fadeThrough;
  index.value = (index.value + direction + props.images.length) % props.images.length;
}
watch(index, () => {
  reset();
  failed.value = false;
});
function onKey(e: KeyboardEvent) {
  if (e.key === 'ArrowLeft') step(-1);
  else if (e.key === 'ArrowRight') step(1);
  else if (e.key === '+' || e.key === '=') zoomTo(view.scale * 1.5);
  else if (e.key === '-') zoomTo(view.scale / 1.5);
  else if (e.key === '0') reset();
  else return;
  e.preventDefault();
}

// A window resized while zoomed would leave the fitted size stale: it starts again from fitting.
const onResize = () => zoomed.value && reset();
onMounted(() => window.addEventListener('resize', onResize, { passive: true }));
onBeforeUnmount(() => window.removeEventListener('resize', onResize));

const figure = ref<HTMLElement | null>(null);
const motionStyle = ref<Record<string, string>>({});
watch(
  open,
  async (value) => {
    if (!value) return;
    reset();
    failed.value = false;
    motion.value = TRANSITIONS.container;
    motionStyle.value = {};
    await nextTick();
    const f = figure.value;
    const box = stage.value?.getBoundingClientRect();
    // Offsets ignore the enter transform already applied, unlike getBoundingClientRect().
    motionStyle.value = f && box ? containerFrom(props.fromRect, new DOMRect(box.left + f.offsetLeft, box.top + f.offsetTop, f.offsetWidth, f.offsetHeight)) : {};
  },
  { immediate: true },
);

function fileName(src: string): string {
  const type = /^data:image\/([\w+.-]+)/i.exec(src)?.[1]?.replace('svg+xml', 'svg');
  if (type) return `image.${type}`;
  try {
    return decodeURIComponent(new URL(src, window.location.href).pathname.split('/').pop() || '') || 'image';
  } catch {
    return 'image';
  }
}
function download() {
  if (!current.value) return;
  const a = document.createElement('a');
  a.href = current.value.src;
  a.download = current.value.alt && !/\s/.test(current.value.alt) && current.value.alt.includes('.') ? current.value.alt : fileName(current.value.src);
  a.rel = 'noopener';
  document.body.append(a);
  a.click();
  a.remove();
}
const openOriginal = () => current.value && window.open(current.value.src, '_blank', 'noopener,noreferrer');
</script>

<template>
  <Teleport to="body">
    <Transition name="scrim">
      <div v-if="open" class="backdrop" :style="{ zIndex }" />
    </Transition>
    <Transition name="scrim">
      <div v-if="open" ref="panel" class="viewer" role="dialog" aria-modal="true" :aria-label="label.title" tabindex="-1" :style="{ zIndex }" @keydown="onKey">
        <header class="bar">
          <span class="caption">
            <span v-if="many" class="type-label-large num">{{ counter }}</span>
            <span v-if="current?.alt" class="alt type-body-medium" :title="current.alt">{{ current.alt }}</span>
          </span>
          <IconButton :icon="ZoomOut" :label="label.zoomOut" :disabled="!zoomed" @click="zoomTo(view.scale / 1.5)" />
          <IconButton :icon="ZoomIn" :label="label.zoomIn" :disabled="view.scale >= MAX" @click="zoomTo(view.scale * 1.5)" />
          <IconButton v-if="zoomed" :icon="Scan" :label="label.resetZoom" @click="reset" />
          <IconButton :icon="Download" :label="label.download" @click="download" />
          <IconButton v-if="openable" :icon="ExternalLink" :label="label.openOriginal" @click="openOriginal" />
          <IconButton :icon="X" :label="label.close" @click="open = false" />
        </header>
        <div
          ref="stage"
          class="stage"
          :class="{ zoomed }"
          @wheel.prevent="onWheel"
          @dblclick="onDoubleClick"
          @pointerdown="onPointerDown"
          @pointermove="onPointerMove"
          @pointerup="onPointerUp"
          @pointercancel="onPointerUp"
        >
          <Transition :name="motion" mode="out-in" appear>
            <div v-if="current" :key="index" ref="figure" class="figure" :style="motionStyle">
              <p v-if="failed" class="failed type-body-medium"><AppIcon :icon="ImageOff" :size="24" />{{ label.failed }}</p>
              <img v-else ref="picture" class="picture" :src="current.src" :alt="current.alt" :style="pictureStyle" draggable="false" @error="failed = true" />
            </div>
          </Transition>
        </div>
        <template v-if="many">
          <div class="side previous"><IconButton :icon="ChevronLeft" :label="label.previous" tonal @click="step(-1)" /></div>
          <div class="side next"><IconButton :icon="ChevronRight" :label="label.next" tonal @click="step(1)" /></div>
        </template>
      </div>
    </Transition>
  </Teleport>
</template>

<style scoped>
.backdrop { position: fixed; inset: 0; background: color-mix(in srgb, var(--md-sys-color-surface-dim) 94%, transparent); backdrop-filter: blur(8px); }
.viewer { position: fixed; inset: 0; display: flex; flex-direction: column; outline: none; color: var(--md-sys-color-on-surface); }
.bar { display: flex; align-items: center; gap: var(--app-space-1); flex: none; padding: var(--app-space-2) var(--app-space-2) var(--app-space-2) var(--app-space-4); }
.caption { display: flex; align-items: baseline; gap: var(--app-space-3); flex: 1; min-width: 0; }
.alt { min-width: 0; overflow: hidden; white-space: nowrap; text-overflow: ellipsis; color: var(--md-sys-color-on-surface-variant); }
/* The image fits the window under the bar; zoomed, it is dragged about rather than scrolled. */
.stage { position: relative; display: grid; place-items: center; flex: 1; min-height: 0; overflow: hidden; touch-action: none; cursor: zoom-in; padding: 0 calc(2 * var(--app-space-8)) var(--app-space-6); }
.stage.zoomed { cursor: grab; }
/* Zoomed, the image leaves the flow and is centred on this box (then of no size) by its own offsets. */
.figure { position: relative; display: grid; place-items: center; max-width: 100%; max-height: 100%; min-height: 0; }
.picture {
  display: block; max-width: 100%; max-height: calc(100vh - 96px); max-height: calc(100dvh - 96px); object-fit: contain; user-select: none;
  border-radius: var(--md-sys-shape-corner-small); box-shadow: var(--app-elevation-3); background: var(--md-sys-color-surface-container-lowest);
}
.failed { display: flex; align-items: center; gap: var(--app-space-2); margin: 0; color: var(--md-sys-color-on-surface-variant); }
.side { position: absolute; top: 50%; transform: translateY(-50%); }
.previous { left: var(--app-space-3); }
.next { right: var(--app-space-3); }
@media (max-width: 599px) {
  .stage { padding: 0 0 var(--app-space-4); }
  .side { top: auto; bottom: var(--app-space-4); transform: none; }
}
</style>
