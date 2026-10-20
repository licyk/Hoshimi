import { mount } from '@vue/test-utils';
import { createPinia } from 'pinia';
import { describe, expect, it } from 'vitest';
import NetworkSettings from '@/components/NetworkSettings.vue';
import { SelectField, TokenField } from '@/ui';

const stubs = {
  SelectField: true, Switch: true,
  TextField: { props: ['modelValue', 'type'], template: '<input :type="type" :value="modelValue" @input="$emit(\'update:modelValue\', $event.target.value)" />' },
  AppButton: { template: '<button><slot /></button>' },
};

describe('network settings', () => {
  it('saves the first proxy and mode together and clears the password input', async () => {
    const wrapper = mount(NetworkSettings, { props: { value: { mode: 'auto', proxy_configured: false } }, global: { plugins: [createPinia()], stubs } });
    wrapper.findComponent(SelectField).vm.$emit('update:modelValue', 'proxy');
    await wrapper.vm.$nextTick();
    expect(wrapper.emitted('save')).toBeUndefined();
    const input = wrapper.get('input[type="password"]');
    await input.setValue('http://user:secret@proxy.test:8080');
    const token = wrapper.findComponent(TokenField);
    await token.get('button').trigger('click');
    expect(wrapper.emitted('save')).toEqual([[{ mode: 'proxy', proxy: 'http://user:secret@proxy.test:8080' }]]);
    expect((input.element as HTMLInputElement).value).toBe('');
  });

  it('returns provider overrides to inheritance when clearing a stored proxy', async () => {
    const wrapper = mount(NetworkSettings, { props: { provider: true, value: { mode: 'proxy', proxy_configured: true } }, global: { plugins: [createPinia()], stubs } });
    expect((wrapper.get('input[type="password"]').element as HTMLInputElement).value).toBe('');
    wrapper.findComponent(TokenField).vm.$emit('clear');
    expect(wrapper.emitted('save')).toEqual([[{ mode: 'inherit', proxy: null }]]);
  });
});
