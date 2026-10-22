import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { effectScope, nextTick, ref } from 'vue';
import { useElapsed } from '@/ui/useElapsed';

describe('useElapsed', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.setSystemTime(100_000);
  });
  afterEach(() => vi.useRealTimers());

  it('ticks each second from the clock, stops while paused, and holds once it ends', async () => {
    const scope = effectScope();
    const running = ref(true);
    const paused = ref(false);
    const ms = scope.run(() => useElapsed(() => 100_000, () => running.value, () => paused.value))!;
    expect(ms.value).toBe(0);
    vi.advanceTimersByTime(2_500);
    expect(Math.floor(ms.value / 1000)).toBe(2);
    paused.value = true;
    await nextTick();
    vi.advanceTimersByTime(10_000);
    expect(Math.floor(ms.value / 1000)).toBe(2);
    paused.value = false;
    await nextTick();
    vi.advanceTimersByTime(3_000);
    // 2.5 s before the pause and 3 s after it: the ten seconds paused are not counted.
    expect(Math.floor(ms.value / 1000)).toBe(5);
    running.value = false;
    await nextTick();
    const held = ms.value;
    vi.advanceTimersByTime(5_000);
    expect(ms.value).toBe(held);
    scope.stop();
  });

  it('starts over when what it counts from changes', async () => {
    const scope = effectScope();
    const start = ref(100_000);
    const ms = scope.run(() => useElapsed(() => start.value, () => true))!;
    vi.advanceTimersByTime(4_200);
    expect(Math.floor(ms.value / 1000)).toBe(4);
    start.value = Date.now();
    await nextTick();
    expect(ms.value).toBe(0);
    scope.stop();
  });
});
