import { onScopeDispose, ref, watch, type Ref } from 'vue';

/**
 * Milliseconds since ``start()`` while ``running()``, less the time spent ``paused()`` (a reply
 * waiting for the user's approval is not working). It is worked out from the clock at each tick
 * rather than counted up, so it never drifts; it ticks on the whole second it shows, and not at all
 * while the page is hidden (it is worked out afresh when the page comes back). Once ``running()``
 * ends it holds the last value.
 */
export function useElapsed(start: () => number, running: () => boolean, paused: () => boolean = () => false): Ref<number> {
  const value = ref(0);
  let pausedFor = 0;
  let pausedSince: number | null = null;
  let timer: ReturnType<typeof setTimeout> | undefined;

  const measure = (now = Date.now()) => {
    const from = start();
    value.value = from ? Math.max(0, (pausedSince ?? now) - from - pausedFor) : 0;
  };
  const schedule = () => {
    clearTimeout(timer);
    timer = undefined;
    if (!running() || pausedSince !== null || (typeof document !== 'undefined' && document.hidden)) return;
    // To the next whole second shown, and a little after it.
    timer = setTimeout(tick, 1000 - (value.value % 1000) + 20);
  };
  function tick() {
    measure();
    schedule();
  }
  const onVisibility = () => tick();

  watch(
    paused,
    (now, before) => {
      if (now && !before) pausedSince = Date.now();
      else if (!now && pausedSince !== null) {
        pausedFor += Date.now() - pausedSince;
        pausedSince = null;
      }
      tick();
    },
    { immediate: true },
  );
  watch(start, () => {
    pausedFor = 0;
    pausedSince = paused() ? Date.now() : null;
    tick();
  });
  watch(running, tick);
  if (typeof document !== 'undefined') document.addEventListener('visibilitychange', onVisibility);
  onScopeDispose(() => {
    clearTimeout(timer);
    if (typeof document !== 'undefined') document.removeEventListener('visibilitychange', onVisibility);
  });
  return value;
}
