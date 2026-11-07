import { afterEach, describe, expect, it, vi } from 'vitest';
import { applyScrollbars } from '@/theme/scrollbars';

describe('scrollbar lanes', () => {
  afterEach(() => {
    vi.restoreAllMocks();
    document.documentElement.removeAttribute('data-scrollbars');
    document.documentElement.style.removeProperty('--app-scroll-lane');
    document.documentElement.style.removeProperty('--app-scroll-overlay');
    document.documentElement.style.removeProperty('--app-scrollbar-size');
  });

  function measure(nativeLane: number) {
    vi.spyOn(HTMLElement.prototype, 'offsetWidth', 'get').mockReturnValue(100);
    vi.spyOn(HTMLElement.prototype, 'clientWidth', 'get').mockReturnValue(100 - nativeLane);
    return applyScrollbars();
  }

  it('reserves padding when native scrollbars occupy no layout space', () => {
    expect(measure(0)).toBe('overlay');
    expect(document.documentElement.style.getPropertyValue('--app-scroll-lane')).toBe('12px');
    expect(document.documentElement.style.getPropertyValue('--app-scroll-overlay')).toBe('12px');
    expect(document.body.querySelector('.app-scroll')).toBeNull();
  });

  it('does not add a second lane when the browser already reserves one', () => {
    expect(measure(10)).toBe('classic');
    expect(document.documentElement.style.getPropertyValue('--app-scroll-lane')).toBe('10px');
    expect(document.documentElement.style.getPropertyValue('--app-scroll-overlay')).toBe('0px');
  });

  it('clears overlay compensation when measured again with a native gutter', () => {
    measure(0);
    measure(12);
    expect(document.documentElement.dataset.scrollbars).toBe('classic');
    expect(document.documentElement.style.getPropertyValue('--app-scroll-overlay')).toBe('0px');
    expect(document.body.querySelector('.app-scroll')).toBeNull();
  });
});
