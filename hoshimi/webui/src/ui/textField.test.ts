import { flushPromises, mount } from '@vue/test-utils';
import { describe, expect, it, vi } from 'vitest';
import { h } from 'vue';
import IconButton from '@/ui/IconButton.vue';
import TextField from '@/ui/TextField.vue';
import { Eye } from '@/ui/icons';

describe('text field trailing actions', () => {
  it.each([false, true])('defaults to a plain button and still accepts an explicit submit type (tonal=%s)', async (tonal) => {
    const submit = vi.fn((event: Event) => event.preventDefault());
    const w = mount({ render: () => h('form', { onSubmit: submit }, [h(IconButton, { icon: Eye, label: 'Show key', tonal })]) });
    const tag = tonal ? 'md-filled-tonal-icon-button' : 'md-icon-button';
    expect((w.find(tag).element as unknown as { type: string }).type).toBe('button');
    expect(w.find(tag).attributes('aria-pressed')).toBeUndefined();
    await w.find(tag).trigger('click');
    await flushPromises();
    expect(submit).not.toHaveBeenCalled();
    w.unmount();
    const explicit = mount(IconButton, { props: { icon: Eye, label: 'Save', tonal, type: 'submit' } });
    expect((explicit.find(tag).element as unknown as { type: string }).type).toBe('submit');
    explicit.unmount();
  });

  it('slots the action inside the field and does not treat its Enter key as input submission', async () => {
    const w = mount(TextField, {
      props: { label: 'API key', type: 'password', modelValue: 'test-key' },
      slots: { trailing: () => h(IconButton, { icon: Eye, label: 'Show key', pressed: false }) },
    });
    const field = w.find('md-outlined-text-field');
    const button = field.find('[slot="trailing-icon"] md-icon-button');
    expect(button.attributes('aria-pressed')).toBe('false');
    await button.trigger('keydown', { key: 'Enter' });
    expect(w.emitted('enter')).toBeUndefined();
    await field.trigger('keydown', { key: 'Enter' });
    expect(w.emitted('enter')).toHaveLength(1);
    expect(w.emitted('update:modelValue')).toBeUndefined();
    w.unmount();
  });
});
