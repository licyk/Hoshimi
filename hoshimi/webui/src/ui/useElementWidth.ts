import { onBeforeUnmount, ref, watch, type Ref } from 'vue';

/**
 * An element's content width, kept current as it changes: a pane beside the rail, a side sheet
 * opening, the window resized. A kept-alive page that is not shown measures 0, which is ignored so
 * the page comes back laid out as it was rather than collapsed. An SVG element is measured by its
 * box (a chart drawn at its own width).
 */
export function useElementWidth(target: Ref<Element | null | undefined>, initial = 0) {
  const width = ref(initial);
  let observer: ResizeObserver | null = null;
  const measure = (el: Element) => {
    const w = el.clientWidth || el.getBoundingClientRect().width;
    if (w) width.value = w;
  };
  watch(
    target,
    (el) => {
      observer?.disconnect();
      observer = null;
      if (!el) return;
      measure(el);
      if (typeof ResizeObserver === 'undefined') return;
      observer = new ResizeObserver(() => measure(el));
      observer.observe(el);
    },
    { immediate: true, flush: 'post' },
  );
  onBeforeUnmount(() => observer?.disconnect());
  return width;
}
