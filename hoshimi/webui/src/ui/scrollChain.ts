import { onBeforeUnmount, watch, type Ref } from 'vue';

/** Pixels a wheel's line is worth, for a wheel that counts in lines (``deltaMode`` 1). */
const LINE = 16;

/** Whether ``el`` scrolls down, and has something to scroll. */
function scrollsDown(el: HTMLElement): boolean {
  const overflow = getComputedStyle(el).overflowY;
  return (overflow === 'auto' || overflow === 'scroll') && el.scrollHeight > el.clientHeight + 1;
}

/** How far ``el`` can still move in ``delta``'s direction: positive down, negative up. */
function room(el: HTMLElement, delta: number): number {
  return delta > 0 ? Math.max(0, el.scrollHeight - el.clientHeight - el.scrollTop) : -el.scrollTop;
}

/**
 * One turn of a wheel over ``root``, run through the areas that scroll inside it: the innermost
 * takes what it can, and what it cannot goes on to the one around it, out to ``root``.
 *
 * Browsers keep a gesture on the area it started in (scroll latching), and ``app-scroll`` areas
 * contain their overscroll, so a trackpad swipe that runs a tool's output to its end would stop
 * there, and the conversation around it would not move until a new gesture. While the innermost
 * area takes the whole turn the browser scrolls it as usual; only a turn that reaches an end is
 * shared out here. A sideways turn, a zoom (Ctrl) and Shift (a sideways wheel) are left alone.
 */
export function chainWheel(root: HTMLElement, e: WheelEvent): void {
  if (e.defaultPrevented || e.ctrlKey || e.shiftKey || !e.deltaY || Math.abs(e.deltaX) > Math.abs(e.deltaY)) return;
  const chain: HTMLElement[] = [];
  for (let el = e.target instanceof Element ? e.target : null; el && el !== root; el = el.parentElement) {
    if (el instanceof HTMLElement && scrollsDown(el)) chain.push(el);
  }
  // Over the conversation itself, or an inner area that takes the whole turn: the browser's own.
  if (!chain.length) return;
  let delta = e.deltaMode === 1 ? e.deltaY * LINE : e.deltaMode === 2 ? e.deltaY * root.clientHeight : e.deltaY;
  if (Math.abs(room(chain[0], delta)) >= Math.abs(delta)) return;
  e.preventDefault();
  for (const el of [...chain, root]) {
    const free = room(el, delta);
    const take = delta > 0 ? Math.min(delta, free) : Math.max(delta, free);
    if (take) el.scrollTop += take;
    delta -= take;
    if (!delta) break;
  }
}

/**
 * Let a wheel scroll through the areas inside ``root`` (a tool's output, a summary) and on to
 * ``root`` itself without stopping at their ends (see :func:`chainWheel`). Touch hands on by
 * itself where an area lets it (``app-scroll-chain``).
 */
export function useScrollChain(root: Ref<HTMLElement | null>): void {
  const onWheel = (e: WheelEvent) => root.value && chainWheel(root.value, e);
  let attached: HTMLElement | null = null;
  const detach = () => {
    attached?.removeEventListener('wheel', onWheel);
    attached = null;
  };
  watch(
    root,
    (el) => {
      detach();
      if (!el) return;
      // Not passive: a turn shared out is the page's to scroll, not the browser's as well.
      el.addEventListener('wheel', onWheel, { passive: false });
      attached = el;
    },
    { immediate: true },
  );
  onBeforeUnmount(detach);
}
