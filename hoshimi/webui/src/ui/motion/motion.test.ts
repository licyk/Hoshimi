import { mount } from '@vue/test-utils';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { defineComponent, h, nextTick, ref, Transition } from 'vue';
import Collapse from '@/ui/Collapse.vue';
import { chipRowHooks, milliseconds, morphHeight, prefersReducedMotion, sectionHooks, TRANSITIONS, useMorphWidth } from '@/ui/motion/transitions';

const SRC = join(process.cwd(), 'src');

function useStylesheet(path: string) {
  const style = document.createElement('style');
  style.textContent = readFileSync(join(SRC, path), 'utf8');
  document.head.appendChild(style);
  return () => style.remove();
}

/** A leaving element must be removed even when the transition never reports an end (zero duration). */
async function smoke() {
  const shown = ref(true);
  const Comp = defineComponent(() => () => h(Transition, { name: TRANSITIONS.fadeThrough }, () => (shown.value ? h('div', { class: 'box' }, 'x') : null)));
  const wrapper = mount(Comp, { attachTo: document.body });
  expect(wrapper.find('.box').exists()).toBe(true);
  shown.value = false;
  await nextTick();
  await new Promise((r) => setTimeout(r, 30));
  expect(wrapper.find('.box').exists()).toBe(false);
  shown.value = true;
  await nextTick();
  expect(wrapper.find('.box').exists()).toBe(true);
  wrapper.unmount();
}

describe('motion', () => {
  let cleanup: (() => void)[] = [];
  afterEach(() => {
    cleanup.forEach((f) => f());
    cleanup = [];
    delete document.documentElement.dataset.motion;
  });

  it('runs with full motion under the zero-duration test stylesheet', async () => {
    cleanup.push(useStylesheet('ui/motion/motion.css'), useStylesheet('test/motion-zero.css'));
    expect(prefersReducedMotion()).toBe(false);
    await smoke();
  });

  it('runs with reduced motion, from the in-app choice', async () => {
    cleanup.push(useStylesheet('ui/motion/motion.css'), useStylesheet('test/motion-zero.css'));
    document.documentElement.dataset.motion = 'reduced';
    expect(prefersReducedMotion()).toBe(true);
    await smoke();
  });

  it('defines the reduced-motion rules for the system setting and for the in-app choice', () => {
    const css = readFileSync(join(SRC, 'ui/motion/motion.css'), 'utf8');
    expect(css).toContain('@media (prefers-reduced-motion: reduce)');
    expect(css).toContain(":root[data-motion='reduced'] [class*='-enter-active']");
    for (const name of Object.values(TRANSITIONS)) expect(css).toContain(`.${name}-enter`);
  });

  it('wraps destination changes in a view transition where the browser has one', async () => {
    const { createRouter, createMemoryHistory } = await import('vue-router');
    const { installViewTransitions } = await import('@/ui/motion/viewTransition');
    const calls: string[] = [];
    (document as unknown as { startViewTransition: unknown }).startViewTransition = (cb: () => Promise<void>) => {
      calls.push('start');
      // As browsers do: the update callback runs after the old state is captured (a task later).
      const updateCallbackDone = new Promise<void>((r) => setTimeout(r, 0)).then(cb).then(() => {
        calls.push('done');
      });
      return { finished: updateCallbackDone, ready: updateCallbackDone, updateCallbackDone };
    };
    const C = defineComponent(() => () => h('div'));
    const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/a', name: 'a', component: C }, { path: '/b', name: 'b', component: C }] });
    const supported = installViewTransitions(router);
    expect(supported.value).toBe(true);
    await router.push('/a');
    await router.push('/b');
    await new Promise((r) => setTimeout(r, 50));
    expect(calls).toEqual(['start', 'done']);
    // Reduced motion still changes destinations through a (short, CSS-defined) view transition.
    document.documentElement.dataset.motion = 'reduced';
    await router.push('/a');
    await new Promise((r) => setTimeout(r, 50));
    expect(calls).toEqual(['start', 'done', 'start', 'done']);
    delete document.documentElement.dataset.motion;
    delete (document as unknown as { startViewTransition?: unknown }).startViewTransition;
  });

  it('morphs the pair it names both ways, and drops the names when the transition ends', async () => {
    const { createRouter, createMemoryHistory } = await import('vue-router');
    const { installViewTransitions, morphStyle } = await import('@/ui/motion/viewTransition');
    const seen: Record<string, string>[] = [];
    (document as unknown as { startViewTransition: unknown }).startViewTransition = (cb: () => Promise<void>) => {
      seen.push(morphStyle('card:1'));
      const updateCallbackDone = new Promise<void>((r) => setTimeout(r, 0)).then(cb);
      return { finished: updateCallbackDone, ready: updateCallbackDone, updateCallbackDone };
    };
    const C = defineComponent(() => () => h('div'));
    const routes = [{ path: '/list', name: 'list', component: C }, { path: '/item/:id', name: 'item', component: C }, { path: '/other', name: 'other', component: C }];
    const router = createRouter({ history: createMemoryHistory(), routes });
    installViewTransitions(router, {
      morph: (to, from) => (to.name === 'item' && from.name === 'list' ? `card:${String(to.params.id)}` : from.name === 'item' && to.name === 'list' ? `card:${String(from.params.id)}` : null),
    });
    await router.push('/list');
    for (const path of ['/item/1', '/list', '/other']) {
      await router.push(path);
      await new Promise((r) => setTimeout(r, 20));
    }
    const morph = { viewTransitionName: 'morph', viewTransitionGroup: 'nearest' };
    expect(seen).toEqual([morph, morph, {}]);
    expect(morphStyle('card:1')).toEqual({});
    // Reduced motion names no pair: the page's crossfade alone.
    document.documentElement.dataset.motion = 'reduced';
    await router.push('/list');
    await router.push('/item/1');
    await new Promise((r) => setTimeout(r, 20));
    expect(seen.at(-1)).toEqual({});
    delete document.documentElement.dataset.motion;
    delete (document as unknown as { startViewTransition?: unknown }).startViewTransition;
  });

  it('opens and closes a section with the gap beside it, so the blocks after it slide', () => {
    // A flex column with a 16px gap: block, section, block.
    const column = document.createElement('div');
    Object.assign(column.style, { display: 'flex', flexDirection: 'column', rowGap: '16px' });
    const [before, section, after] = ['div', 'div', 'div'].map((tag) => document.createElement(tag));
    column.append(before!, section!, after!);
    document.body.append(column);
    const el = section!;
    Object.defineProperty(el, 'scrollHeight', { value: 120 });
    Object.defineProperty(el, 'offsetHeight', { get: () => (el.style.height === '0px' ? 0 : 120) });

    sectionHooks.onEnter(el);
    expect([el.style.height, el.style.marginTop]).toEqual(['120px', '0px']);
    sectionHooks.onAfterEnter(el);
    expect([el.style.height, el.style.marginTop]).toEqual(['', '']);
    sectionHooks.onBeforeLeave(el);
    sectionHooks.onLeave(el);
    // Closed, it takes the gap before it back: the column is as it was without it.
    expect([el.style.height, el.style.marginTop]).toEqual(['0px', '-16px']);

    // The first block gives back the gap after it instead.
    before!.remove();
    el.removeAttribute('style');
    sectionHooks.onLeave(el);
    expect([el.style.marginTop, el.style.marginBottom]).toEqual(['', '-16px']);
    column.remove();
  });

  it('opens and closes a chip in a row with the gap beside it, and takes no presses while it goes', () => {
    // A flex row with a 2px gap: chip, chip, the chip that comes and goes last.
    const row = document.createElement('div');
    Object.assign(row.style, { display: 'flex', columnGap: '2px' });
    const [first, second, chip] = ['span', 'span', 'span'].map((tag) => document.createElement(tag));
    row.append(first!, second!, chip!);
    document.body.append(row);
    const el = chip!;
    Object.defineProperty(el, 'offsetWidth', { get: () => (el.style.width === '0px' ? 0 : 84) });

    chipRowHooks.onEnter(el);
    expect([el.style.width, el.style.marginInlineStart, el.style.overflow]).toEqual(['84px', '0px', 'clip']);
    chipRowHooks.onAfterEnter(el);
    expect([el.style.width, el.style.marginInlineStart, el.style.overflow]).toEqual(['', '', '']);
    chipRowHooks.onBeforeLeave(el);
    expect(el.inert).toBe(true);
    expect(el.style.width).toBe('84px');
    chipRowHooks.onLeave(el);
    // Closed, it takes the gap before it back: the row is as it was without it.
    expect([el.style.width, el.style.marginInlineStart]).toEqual(['0px', '-2px']);

    // The first in its row gives back the gap after it instead.
    first!.remove();
    second!.remove();
    row.append(document.createElement('span'));
    el.removeAttribute('style');
    chipRowHooks.onLeave(el);
    expect([el.style.marginInlineStart, el.style.marginInlineEnd]).toEqual(['', '-2px']);

    // Under reduced motion it only fades: its width is left alone.
    document.documentElement.dataset.motion = 'reduced';
    el.removeAttribute('style');
    chipRowHooks.onEnter(el);
    chipRowHooks.onLeave(el);
    expect(el.style.width).toBe('');
    delete document.documentElement.dataset.motion;
    row.remove();
  });

  it('mounts and removes a chip-row control under the zero-duration test stylesheet', async () => {
    cleanup.push(useStylesheet('ui/motion/motion.css'), useStylesheet('test/motion-zero.css'));
    const shown = ref(false);
    const Comp = defineComponent(() => () =>
      h('div', { style: 'display: flex; gap: 2px' }, [h('span', 'model'), h(Transition, { name: TRANSITIONS.chipRow, ...chipRowHooks }, () => (shown.value ? h('span', { class: 'chip' }, 'Ask') : null))]),
    );
    const wrapper = mount(Comp, { attachTo: document.body });
    shown.value = true;
    await nextTick();
    await new Promise((r) => setTimeout(r, 30));
    expect((wrapper.find('.chip').element as HTMLElement).style.width).toBe('');
    shown.value = false;
    await nextTick();
    await new Promise((r) => setTimeout(r, 30));
    expect(wrapper.find('.chip').exists()).toBe(false);
    wrapper.unmount();
  });

  it('mounts and removes a Collapse section under the zero-duration test stylesheet', async () => {
    cleanup.push(useStylesheet('ui/motion/motion.css'), useStylesheet('test/motion-zero.css'));
    const shown = ref(false);
    const Comp = defineComponent(() => () => h('div', h(Collapse, { show: shown.value }, () => h('p', { class: 'inside' }, 'x'))));
    const wrapper = mount(Comp, { attachTo: document.body });
    expect(wrapper.find('.inside').exists()).toBe(false);
    shown.value = true;
    await nextTick();
    await new Promise((r) => setTimeout(r, 30));
    expect(wrapper.find('.inside').exists()).toBe(true);
    // Once open, the box sizes to its content again.
    expect((wrapper.find('.collapse').element as HTMLElement).style.height).toBe('');
    shown.value = false;
    await nextTick();
    await new Promise((r) => setTimeout(r, 30));
    expect(wrapper.find('.inside').exists()).toBe(false);
    wrapper.unmount();
  });

  it('gives reduced motion a short crossfade between destinations', () => {
    const css = readFileSync(join(SRC, 'ui/motion/motion.css'), 'utf8');
    expect(css).toContain(":root[data-motion='reduced']::view-transition-new(app-content)");
  });
});

describe('morphHeight', () => {
  afterEach(() => {
    delete document.documentElement.dataset.motion;
  });

  it('reads a CSS time in either unit', () => {
    expect(milliseconds('300ms')).toBe(300);
    expect(milliseconds('.3s')).toBe(300);
    expect(milliseconds(' 0.25s ')).toBe(250);
    expect(milliseconds('')).toBeNaN();
  });

  it('eases the box from its old height to the one it has once the change has settled', async () => {
    const el = document.createElement('div');
    const heights = [196, 224];
    el.getBoundingClientRect = () => ({ height: heights.shift() ?? 224 }) as DOMRect;
    el.animate = vi.fn() as unknown as typeof el.animate;
    const order: string[] = [];
    await morphHeight(el, async () => {
      order.push('change');
      await Promise.resolve();
      order.push('settled');
    });
    expect(order).toEqual(['change', 'settled']);
    const [frames, options] = vi.mocked(el.animate).mock.calls[0] as [Keyframe[], KeyframeAnimationOptions];
    expect(frames.map((f) => f.height)).toEqual(['196px', '224px']);
    expect(options.duration).toBeGreaterThan(100);
  });

  it('does not animate under reduced motion, or when the height stays', async () => {
    const el = document.createElement('div');
    el.getBoundingClientRect = () => ({ height: 100 }) as DOMRect;
    el.animate = vi.fn() as unknown as typeof el.animate;
    await morphHeight(el, () => undefined);
    document.documentElement.dataset.motion = 'reduced';
    let h = 100;
    el.getBoundingClientRect = () => ({ height: (h += 50) }) as DOMRect;
    await morphHeight(el, () => undefined);
    expect(el.animate).not.toHaveBeenCalled();
  });
});

describe('useMorphWidth', () => {
  afterEach(() => {
    delete document.documentElement.dataset.motion;
  });

  /** A chip whose words come from ``text``, its width following them, eased by ``useMorphWidth``. */
  function chip(text: ReturnType<typeof ref<string>>) {
    const box = ref<HTMLElement | null>(null);
    const Comp = defineComponent(() => {
      useMorphWidth(box, () => text.value);
      return () => h('span', { ref: box }, text.value);
    });
    const wrapper = mount(Comp);
    const el = wrapper.element as HTMLElement;
    Object.defineProperty(el, 'offsetWidth', { get: () => 10 * (el.textContent?.length ?? 0) });
    el.animate = vi.fn() as unknown as typeof el.animate;
    return { el, wrapper };
  }

  it('eases a chip from its old width to the one its new words take', async () => {
    const text = ref('Off');
    const { el, wrapper } = chip(text);
    text.value = 'Full access';
    await nextTick();
    const [frames] = vi.mocked(el.animate).mock.calls[0] as [Keyframe[], KeyframeAnimationOptions];
    expect(frames.map((f) => f.width)).toEqual(['30px', '110px']);
    expect(frames.every((f) => f.overflow === 'clip')).toBe(true);
    wrapper.unmount();
  });

  it('leaves the width alone under reduced motion, or when it stays', async () => {
    const text = ref('Ask');
    const { el, wrapper } = chip(text);
    text.value = 'Low';
    await nextTick();
    document.documentElement.dataset.motion = 'reduced';
    text.value = 'High!';
    await nextTick();
    expect(el.animate).not.toHaveBeenCalled();
    wrapper.unmount();
  });
});
