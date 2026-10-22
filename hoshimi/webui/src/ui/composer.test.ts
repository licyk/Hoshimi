import { flushPromises, mount } from '@vue/test-utils';
import { describe, expect, it } from 'vitest';
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

describe('ComposerMenu', () => {
  const options = [
    { value: 'a/one', label: 'a/one', description: 'One · alpha' },
    { value: 'b/two', label: 'b/two', description: 'Two · beta' },
    { value: 'b/three', label: 'b/three', description: 'Three · beta' },
  ];

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
