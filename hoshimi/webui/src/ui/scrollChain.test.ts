import { describe, expect, it } from 'vitest';
import { chainWheel } from '@/ui/scrollChain';

/** An area that scrolls down: ``height`` of it shown, ``content`` in all, at ``top``. */
function area(height: number, content: number, top = 0): HTMLElement {
  const el = document.createElement('div');
  el.style.overflowY = 'auto';
  Object.defineProperty(el, 'clientHeight', { value: height });
  Object.defineProperty(el, 'scrollHeight', { value: content });
  el.scrollTop = top;
  return el;
}

/** The conversation (600 of 3000, at 1000) holding a tool's output (300 of 1000, at ``top``), with a line of text in it. */
function nested(top: number) {
  const root = area(600, 3000, 1000);
  const output = area(300, 1000, top);
  const text = document.createElement('span');
  output.append(text);
  root.append(output);
  document.body.append(root);
  return { root, output, text };
}

function wheel(target: Element, init: WheelEventInit): WheelEvent {
  const e = new WheelEvent('wheel', { bubbles: true, cancelable: true, ...init });
  Object.defineProperty(e, 'target', { value: target });
  // happy-dom's WheelEvent drops the modifier keys it is given.
  for (const key of ['ctrlKey', 'shiftKey'] as const) Object.defineProperty(e, key, { value: !!init[key] });
  return e;
}

describe('chainWheel', () => {
  it('leaves a turn the inner area takes whole to the browser', () => {
    const { root, output, text } = nested(100);
    const e = wheel(text, { deltaY: 120 });
    chainWheel(root, e);
    expect([e.defaultPrevented, output.scrollTop, root.scrollTop]).toEqual([false, 100, 1000]);
  });

  it('hands what the inner area cannot take on to the conversation, in the same turn', () => {
    // 30 left of the output: it takes those, the conversation the other 90.
    const { root, output, text } = nested(670);
    const e = wheel(text, { deltaY: 120 });
    chainWheel(root, e);
    expect([e.defaultPrevented, output.scrollTop, root.scrollTop]).toEqual([true, 700, 1090]);
    // At its end, all of the next turn goes on.
    chainWheel(root, wheel(text, { deltaY: 120 }));
    expect([output.scrollTop, root.scrollTop]).toEqual([700, 1210]);
  });

  it('hands on upward from the top too, and counts a wheel that turns in lines', () => {
    const { root, output, text } = nested(0);
    chainWheel(root, wheel(text, { deltaY: -3, deltaMode: 1 }));
    expect([output.scrollTop, root.scrollTop]).toEqual([0, 952]);
  });

  it('leaves alone a sideways turn, a zoom, Shift, and the conversation outside any inner area', () => {
    const { root, output, text } = nested(700);
    for (const init of [{ deltaY: 10, deltaX: 40 }, { deltaY: 120, ctrlKey: true }, { deltaY: 120, shiftKey: true }]) {
      const e = wheel(text, init);
      chainWheel(root, e);
      expect(e.defaultPrevented).toBe(false);
    }
    const outside = wheel(root, { deltaY: 120 });
    chainWheel(root, outside);
    expect([outside.defaultPrevented, output.scrollTop, root.scrollTop]).toEqual([false, 700, 1000]);
  });
});
