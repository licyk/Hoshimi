/** Named transitions and helpers for the motion system. See motion.css. */
import { nextTick, ref, watch, type Ref } from 'vue';

export const TRANSITIONS = {
  fadeThrough: 'fade-through',
  sharedAxisX: 'shared-axis-x',
  sharedAxisY: 'shared-axis-y',
  container: 'container',
  sheet: 'sheet',
  scrim: 'scrim',
  list: 'list',
  collapse: 'collapse',
  section: 'section',
  snackbar: 'snackbar',
  popover: 'popover',
  chip: 'chip',
  chipRow: 'chip-row',
  iconSwap: 'icon-swap',
} as const;

const STAGGER_MS = 20;
const STAGGER_CAP = 12;

/** Style for the n-th item of a list transition: a 20 ms stagger, capped. */
export function staggerStyle(index: number): Record<string, string> {
  return { '--stagger': `${Math.min(index, STAGGER_CAP) * STAGGER_MS}ms` };
}

/**
 * The way a ``shared-axis-x`` tab change moves, for ``--axis-dir`` (as Hanaikada's tag tabs): 1 to a
 * tab on the right, -1 to one on the left. Set synchronously, before the panes re-render.
 */
export function useAxisDirection<T>(value: Ref<T>, order: () => readonly T[]): Ref<number> {
  const dir = ref(1);
  watch(value, (next, prev) => (dir.value = order().indexOf(next) >= order().indexOf(prev) ? 1 : -1), { flush: 'sync' });
  return dir;
}

/**
 * Whether a meter's value last went down, for audio-meter ballistics: a rise shows at once, a fall
 * settles over a longer transition. Set synchronously, with the value it describes.
 */
export function useFalling(value: () => number): Ref<boolean> {
  const falling = ref(false);
  watch(value, (next, prev) => (falling.value = next < prev), { flush: 'sync' });
  return falling;
}

export function prefersReducedMotion(): boolean {
  if (typeof window === 'undefined') return false;
  if (document.documentElement.dataset.motion === 'reduced') return true;
  return !!window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;
}

/**
 * Change what a box holds and ease its height from what it was to what it becomes, instead of
 * letting it jump: a question turning into its editor and back. ``change`` swaps the content (and
 * may wait for it to settle, a text box fitted to its text); the new content fades in on its own
 * (the ``morph-in`` class). Under reduced motion the box just takes its new height.
 */
/** A CSS time as milliseconds: the browser may give back ``300ms`` as ``.3s``. */
export function milliseconds(time: string): number {
  const value = parseFloat(time);
  return /\ds\s*$/.test(time.trim()) && !/ms\s*$/.test(time.trim()) ? value * 1000 : value;
}

export async function morphHeight(el: HTMLElement | null | undefined, change: () => unknown): Promise<void> {
  const from = el?.getBoundingClientRect().height ?? 0;
  await change();
  await nextTick();
  if (!el || prefersReducedMotion() || typeof el.animate !== 'function') return;
  const to = el.getBoundingClientRect().height;
  if (Math.abs(to - from) < 1) return;
  const root = getComputedStyle(document.documentElement);
  const duration = milliseconds(root.getPropertyValue('--md-sys-motion-duration-medium2')) || 300;
  const easing = root.getPropertyValue('--md-sys-motion-easing-emphasized').trim() || 'cubic-bezier(0.2, 0, 0, 1)';
  // The heights were read with any cap the box has; lifted while it moves, so a capped box can close to it.
  const frame = { overflow: 'hidden', maxHeight: 'none' };
  el.animate([{ ...frame, height: `${from}px` }, { ...frame, height: `${to}px` }], { duration, easing });
}

/**
 * CSS variables that make a dialog grow from ``from`` (a card's rectangle) into ``to``.
 * Returns an empty object when the source rectangle is unknown, which falls back to fade and scale.
 */
export function containerFrom(from: DOMRect | null | undefined, to: DOMRect | null | undefined): Record<string, string> {
  if (!from || !to || to.width === 0 || to.height === 0) return {};
  const scaleX = from.width / to.width;
  const scaleY = from.height / to.height;
  const dx = from.left - to.left;
  const dy = from.top - to.top;
  return {
    '--from-origin': 'top left',
    '--from-transform': `translate(${dx}px, ${dy}px) scale(${scaleX}, ${scaleY})`,
  };
}

/**
 * Hooks for a ``list`` <TransitionGroup> of rows (its container ``position: relative``): the leaving
 * item is taken out of the flow (motion.css), so it is pinned where it was, at its own size, rather
 * than reflowing as a box without a width (its fields wrapping into one another) while it fades and
 * the others slide up.
 */
export const keepLeavingInPlace = {
  onBeforeLeave(el: Element) {
    const e = el as HTMLElement;
    Object.assign(e.style, { left: `${e.offsetLeft}px`, top: `${e.offsetTop}px`, width: `${e.offsetWidth}px`, height: `${e.offsetHeight}px` });
  },
};

/** Hooks for <Transition name="collapse"> that animate height from and to the content's size. */
export const collapseHooks = {
  onBeforeEnter(el: Element) {
    (el as HTMLElement).style.height = '0';
  },
  onEnter(el: Element) {
    const e = el as HTMLElement;
    e.style.height = `${e.scrollHeight}px`;
  },
  onAfterEnter(el: Element) {
    (el as HTMLElement).style.height = '';
  },
  onBeforeLeave(el: Element) {
    const e = el as HTMLElement;
    e.style.height = `${e.scrollHeight}px`;
  },
  onLeave(el: Element) {
    const e = el as HTMLElement;
    void e.offsetHeight;
    e.style.height = '0';
  },
};

/**
 * Ease ``el``'s width from what it was to what it is whenever ``source`` changes (a chip whose words
 * change: "Off" becoming "2", "Ask" becoming "Full access"), rather than letting the row jump. The
 * width is read before the change renders and after; under reduced motion it just takes its new width.
 */
export function useMorphWidth(el: Ref<HTMLElement | null | undefined>, source: () => unknown): void {
  let from = 0;
  watch(source, () => (from = el.value?.offsetWidth ?? 0), { flush: 'pre' });
  watch(
    source,
    () => {
      const e = el.value;
      if (!e || !from || prefersReducedMotion() || typeof e.animate !== 'function') return;
      const to = e.offsetWidth;
      if (Math.abs(to - from) < 1) return;
      const root = getComputedStyle(document.documentElement);
      const duration = milliseconds(root.getPropertyValue('--md-sys-motion-duration-medium2')) || 300;
      const easing = root.getPropertyValue('--md-sys-motion-easing-emphasized').trim() || 'cubic-bezier(0.2, 0, 0, 1)';
      e.animate([{ width: `${from}px`, overflow: 'clip' }, { width: `${to}px`, overflow: 'clip' }], { duration, easing });
    },
    { flush: 'post' },
  );
}

type GapSide = 'marginTop' | 'marginBottom';

/**
 * The row gap a flex column or grid puts beside ``el``, and the side it is on: before the block,
 * or after it for the first one. A block that only shrinks to nothing still leaves that gap, which
 * would close in one step at the end; the block's margin takes it in and gives it back instead.
 */
function gapBeside(el: HTMLElement): [side: GapSide, px: number] | null {
  const parent = el.parentElement;
  if (!parent) return null;
  const style = getComputedStyle(parent);
  const stacked = style.display.includes('grid') || (style.display.includes('flex') && style.flexDirection.startsWith('column'));
  const gap = Number.parseFloat(style.rowGap);
  if (!stacked || !gap) return null;
  if (el.previousElementSibling) return ['marginTop', gap];
  return el.nextElementSibling ? ['marginBottom', gap] : null;
}

/**
 * Hooks for <Transition name="section"> on a block of a page that comes and goes (``Collapse``):
 * its height opens from nothing to the content's and closes again, and the gap beside it with it,
 * so the blocks after it slide rather than jump. The block itself must have no padding or border.
 * A change of mind halfway turns back from where the block is, not from either end.
 */
export const sectionHooks = {
  onEnter(el: Element) {
    const e = el as HTMLElement;
    const gap = gapBeside(e);
    e.style.height = '0px';
    if (gap) e.style[gap[0]] = `${-gap[1]}px`;
    // Lay the closed box out first: a box inserted and opened in one go has no start to move from.
    void e.offsetHeight;
    e.style.height = `${e.scrollHeight}px`;
    if (gap) e.style[gap[0]] = '0px';
  },
  onAfterEnter(el: Element) {
    Object.assign((el as HTMLElement).style, { height: '', marginTop: '', marginBottom: '' });
  },
  onBeforeLeave(el: Element) {
    // Where it is now, mid-way through opening or not.
    const e = el as HTMLElement;
    const style = getComputedStyle(e);
    Object.assign(e.style, { height: `${e.offsetHeight}px`, marginTop: style.marginTop, marginBottom: style.marginBottom });
  },
  onLeave(el: Element) {
    const e = el as HTMLElement;
    const gap = gapBeside(e);
    void e.offsetHeight;
    e.style.height = '0px';
    if (gap) e.style[gap[0]] = `${-gap[1]}px`;
  },
};

type InlineGapSide = 'marginInlineStart' | 'marginInlineEnd';

/**
 * The column gap a flex row puts beside ``el``, and the side it is on: before it, or after it for
 * the first one. A control that only narrows to nothing still leaves that gap, which would close in
 * one step at the end; the control's margin takes it in and gives it back instead.
 */
function gapInline(el: HTMLElement): [side: InlineGapSide, px: number] | null {
  const parent = el.parentElement;
  if (!parent) return null;
  const style = getComputedStyle(parent);
  const row = style.display.includes('flex') && !style.flexDirection.startsWith('column');
  const gap = Number.parseFloat(style.columnGap);
  if (!row || !gap) return null;
  if (el.previousElementSibling) return ['marginInlineStart', gap];
  return el.nextElementSibling ? ['marginInlineEnd', gap] : null;
}

/**
 * Hooks for <Transition name="chip-row"> on a control that comes and goes in a row of them (the
 * composer's chips): its width opens from nothing to its own and closes again, the gap beside it
 * with it, so the others slide over and a control that gives way (a long model name) narrows
 * rather than jumping. A change of mind halfway turns back from where it is. The control must have
 * no padding or border of its own (wrap one that does); while it leaves it takes no presses. Under
 * reduced motion it only fades.
 */
export const chipRowHooks = {
  onEnter(el: Element) {
    const e = el as HTMLElement;
    if (prefersReducedMotion()) return;
    const gap = gapInline(e);
    const width = e.offsetWidth;
    e.style.overflow = 'clip';
    e.style.width = '0px';
    if (gap) e.style[gap[0]] = `${-gap[1]}px`;
    // Lay the closed box out first: a box inserted and opened in one go has no start to move from.
    void e.offsetWidth;
    e.style.width = `${width}px`;
    if (gap) e.style[gap[0]] = '0px';
  },
  onAfterEnter(el: Element) {
    Object.assign((el as HTMLElement).style, { width: '', overflow: '', marginInlineStart: '', marginInlineEnd: '' });
  },
  onBeforeLeave(el: Element) {
    const e = el as HTMLElement;
    e.inert = true;
    if (prefersReducedMotion()) return;
    // Where it is now, mid-way through opening or not.
    const style = getComputedStyle(e);
    Object.assign(e.style, { width: `${e.offsetWidth}px`, overflow: 'clip', marginInlineStart: style.marginInlineStart, marginInlineEnd: style.marginInlineEnd });
  },
  onLeave(el: Element) {
    const e = el as HTMLElement;
    if (prefersReducedMotion()) return;
    const gap = gapInline(e);
    void e.offsetWidth;
    e.style.width = '0px';
    if (gap) e.style[gap[0]] = `${-gap[1]}px`;
  },
};
