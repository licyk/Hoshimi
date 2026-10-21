import { onBeforeUnmount, ref, watch, type Ref } from 'vue';
import { prefersReducedMotion } from '@/ui/motion/transitions';

/** Emphasized-decelerate (0.05, 0.7, 0.1, 1), closely enough for a number: fast out, long settle. */
const ease = (t: number) => 1 - Math.pow(1 - t, 4);

/**
 * A number that glides to each new value instead of jumping: a stat tile counting up as live
 * calls finish. The first value shows at once (a page opening does not count up from zero), and
 * under reduced motion every value does. A new target mid-glide starts from where the number is.
 */
export function useTween(source: () => number | null | undefined, duration = 400): Ref<number | null> {
  const shown = ref<number | null>(source() ?? null);
  let frame = 0;
  watch(source, (to, from) => {
    cancelAnimationFrame(frame);
    if (to === null || to === undefined || from === null || from === undefined || shown.value === null || prefersReducedMotion() || typeof requestAnimationFrame === 'undefined') {
      shown.value = to ?? null;
      return;
    }
    const start = shown.value;
    const began = performance.now();
    const step = (now: number) => {
      const t = Math.min(1, (now - began) / duration);
      shown.value = start + (to - start) * ease(t);
      if (t < 1) frame = requestAnimationFrame(step);
    };
    frame = requestAnimationFrame(step);
  });
  onBeforeUnmount(() => cancelAnimationFrame(frame));
  return shown;
}
