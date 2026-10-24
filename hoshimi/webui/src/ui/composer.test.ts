import { flushPromises, mount } from '@vue/test-utils';
import { describe, expect, it, vi } from 'vitest';
import Composer from '@/ui/Composer.vue';
import ComposerMenu from '@/ui/ComposerMenu.vue';

const LABELS = { label: 'Message', sendLabel: 'Send', stopLabel: 'Stop' };

describe('Composer', () => {
  it('sends on Enter only, with text, and leaves Shift+Enter and an input method alone', async () => {
    const wrapper = mount(Composer, { props: { ...LABELS, modelValue: '' } });
    const input = wrapper.find('textarea');
    await input.trigger('keydown', { key: 'Enter' });
    expect(wrapper.emitted('send')).toBeUndefined();
    await wrapper.setProps({ modelValue: 'hello' });
    await input.trigger('keydown', { key: 'Enter', shiftKey: true });
    await input.trigger('keydown', { key: 'Enter', isComposing: true });
    expect(wrapper.emitted('send')).toBeUndefined();
    await input.trigger('keydown', { key: 'Enter' });
    expect(wrapper.emitted('send')).toHaveLength(1);
  });

  it('on a phone, leaves Enter to start a new line and sends only with the button', async () => {
    const touch = vi.spyOn(window, 'matchMedia').mockImplementation((query: string) => ({ matches: query.includes('pointer: coarse'), media: query }) as MediaQueryList);
    try {
      const wrapper = mount(Composer, { props: { ...LABELS, modelValue: 'two\nlines' } });
      const enter = new KeyboardEvent('keydown', { key: 'Enter', bubbles: true, cancelable: true });
      wrapper.find('textarea').element.dispatchEvent(enter);
      expect(enter.defaultPrevented).toBe(false);
      expect(wrapper.emitted('send')).toBeUndefined();
      await wrapper.find('button.send').trigger('click');
      expect(wrapper.emitted('send')).toHaveLength(1);
    } finally {
      touch.mockRestore();
    }
  });

  it('keeps Send disabled until it can send, and turns it into Stop while busy', async () => {
    const wrapper = mount(Composer, { props: { ...LABELS, modelValue: 'hello', canSend: false } });
    expect(wrapper.find('button[aria-label="Send"]').attributes('disabled')).toBeDefined();
    await wrapper.setProps({ canSend: true });
    expect(wrapper.find('button[aria-label="Send"]').attributes('disabled')).toBeUndefined();
    await wrapper.setProps({ busy: true });
    const stop = wrapper.find('button[aria-label="Stop"]');
    expect(stop.attributes('disabled')).toBeUndefined();
    await stop.trigger('click');
    expect(wrapper.emitted('stop')).toHaveLength(1);
    await wrapper.find('textarea').trigger('keydown', { key: 'Enter' });
    expect(wrapper.emitted('send')).toBeUndefined();
  });
});

describe('Composer steering', () => {
  it('sends to the answer under way while there is text, and stops it otherwise', async () => {
    const wrapper = mount(Composer, { props: { ...LABELS, steerLabel: 'Steer', modelValue: '', busy: true, steerable: true } });
    expect(wrapper.find('button[aria-label="Stop"]').exists()).toBe(true);
    await wrapper.setProps({ modelValue: 'also this' });
    await wrapper.find('textarea').trigger('keydown', { key: 'Enter' });
    await wrapper.find('button[aria-label="Steer"]').trigger('click');
    expect(wrapper.emitted('steer')).toHaveLength(2);
    expect(wrapper.emitted('send')).toBeUndefined();
    // Escape still stops; without steering, text does not change the button.
    await wrapper.find('textarea').trigger('keydown', { key: 'Escape' });
    expect(wrapper.emitted('stop')).toHaveLength(1);
    await wrapper.setProps({ steerable: false });
    expect(wrapper.find('button[aria-label="Stop"]').exists()).toBe(true);
    await wrapper.find('textarea').trigger('keydown', { key: 'Enter' });
    expect(wrapper.emitted('steer')).toHaveLength(2);
  });
});

describe('ComposerMenu', () => {
  const options = [
    { value: 'a/one', label: 'a/one', description: 'One · alpha' },
    { value: 'b/two', label: 'b/two', description: 'Two · beta' },
    { value: 'b/three', label: 'b/three', description: 'Three · beta' },
  ];

  it('follows its control while open when the control moves without a scroll or a resize', async () => {
    const frames: FrameRequestCallback[] = [];
    vi.stubGlobal('requestAnimationFrame', (cb: FrameRequestCallback) => frames.push(cb));
    vi.stubGlobal('cancelAnimationFrame', () => frames.splice(0));
    const tick = () => frames.splice(0).forEach((cb) => cb(0));
    const wrapper = mount(ComposerMenu, { props: { label: 'Tools', options, multiple: true, values: [] }, attachTo: document.body });
    let left = 100;
    (wrapper.element as HTMLElement).getBoundingClientRect = () => ({ left, top: 500, bottom: 532, width: 80, height: 32, right: left + 80, x: left, y: 500, toJSON: () => ({}) }) as DOMRect;
    await wrapper.find('button').trigger('click');
    await flushPromises();
    const menu = document.querySelector<HTMLElement>('.menu')!;
    tick();
    expect(menu.style.left).toBe('100px');
    // A chip opening beside it pushes it over: the menu goes with it, a frame later.
    left = 60;
    tick();
    await flushPromises();
    expect(menu.style.left).toBe('60px');
    await wrapper.find('button').trigger('click');
    await flushPromises();
    expect(frames).toHaveLength(0);
    wrapper.unmount();
    vi.unstubAllGlobals();
  });

  it('shows the choice, searches the list and picks with the keyboard', async () => {
    const wrapper = mount(ComposerMenu, { props: { label: 'Model', options, modelValue: 'a/one', searchLabel: 'Search' }, attachTo: document.body });
    const trigger = wrapper.find('button');
    expect(trigger.text()).toContain('a/one');
    expect(trigger.attributes('aria-expanded')).toBe('false');
    await trigger.trigger('click');
    await flushPromises();
    const menu = document.querySelector<HTMLElement>('.menu')!;
    expect(menu.querySelectorAll('[role="option"]')).toHaveLength(3);
    expect(menu.querySelector('[aria-selected="true"]')!.textContent).toContain('a/one');
    const search = menu.querySelector<HTMLInputElement>('input')!;
    search.value = 'beta';
    search.dispatchEvent(new Event('input'));
    await flushPromises();
    expect(menu.querySelectorAll('[role="option"]')).toHaveLength(2);
    search.focus();
    search.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowDown', bubbles: true }));
    search.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
    await flushPromises();
    expect(wrapper.emitted('update:modelValue')).toEqual([['b/three']]);
    expect(document.querySelector('.menu')).toBeNull();
    wrapper.unmount();
  });

  it('does not open while its options are loading', async () => {
    const wrapper = mount(ComposerMenu, { props: { label: 'Model', options: [], loading: true }, attachTo: document.body });
    await wrapper.find('button').trigger('click');
    await flushPromises();
    expect(document.querySelector('.menu')).toBeNull();
    wrapper.unmount();
  });
});
