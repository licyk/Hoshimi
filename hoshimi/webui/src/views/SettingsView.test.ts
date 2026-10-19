import { flushPromises, mount } from '@vue/test-utils';
import { createPinia } from 'pinia';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { ref } from 'vue';
import SettingsView from '@/views/SettingsView.vue';

const state = vi.hoisted(() => ({ update: vi.fn() }));
const configured = (id: string, enabled: boolean) => ({ id, enabled, accounts: 0, options: [] as string[] });
const runtime = (id: string, enabled: boolean) => ({ id, display_name: id, enabled, accounts: 0, requires_authorization: id === 'alpha' });
const settings = ref({ providers: [configured('zeta', true)], server: { allow_cidrs: [], allow_hosts: [], allow_origins: [] } });
const providers = ref([runtime('zeta', true), runtime('alpha', false), runtime('beta', false)]);

vi.mock('@/api/queries/app', () => ({
  useSettings: () => ({ data: settings, isFetching: { value: false } }),
  useProviders: () => ({ data: providers }),
  useMeta: () => ({ data: { value: { auth_required: true } } }),
  useVersion: () => ({ data: { value: { version: 'test' } } }),
  useUpdateSettings: () => ({ mutate: state.update }),
}));
vi.mock('@/ui', () => {
  const slot = { template: '<div><slot /></div>' };
  return {
    icons: {}, Surface: slot, AppButton: slot, DataList: slot, SegmentedButton: slot, SelectField: slot, Skeleton: slot, Slider: slot, TextField: slot, TokenField: slot,
    Badge: { props: ['value'], template: '<span class="badge">{{ value }}</span>' },
    Switch: { props: ['modelValue'], template: '<button class="switch" :aria-pressed="modelValue" @click="$emit(\'update:modelValue\', !modelValue)" />' },
    useSnackbar: () => ({ show: vi.fn(), error: vi.fn() }),
  };
});

describe('settings provider positions', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    settings.value.providers = [configured('zeta', true)];
    providers.value = [runtime('zeta', true), runtime('alpha', false), runtime('beta', false)];
  });

  it('keeps the displayed order when toggles change config membership and API ordering', async () => {
    const wrapper = mount(SettingsView, { global: { plugins: [createPinia()] } });
    const names = () => wrapper.findAll('.provider .name').map((name) => name.text());
    const original = names();
    expect(original).toEqual(['zeta', 'alpha', 'beta']);
    await wrapper.findAll('.provider')[2].find('button').trigger('click');
    expect(state.update.mock.calls[0][0]).toEqual({ providers: { beta: { enabled: true } } });
    // The first save adds beta to the configured rows, ahead of the unconfigured alpha.
    settings.value.providers.push(configured('beta', true));
    providers.value = [runtime('beta', true), runtime('zeta', true), runtime('alpha', false)];
    await flushPromises();
    expect(names()).toEqual(original);
    expect(wrapper.findAll('.provider')[2].find('button').attributes('aria-pressed')).toBe('true');
    await wrapper.findAll('.provider')[0].find('button').trigger('click');
    expect(state.update.mock.calls[1][0]).toEqual({ providers: { zeta: { enabled: false } } });
    settings.value.providers = [configured('beta', true), configured('zeta', false)];
    providers.value = [runtime('beta', true), runtime('alpha', false), runtime('zeta', false)];
    await flushPromises();
    expect(names()).toEqual(original);
    expect(wrapper.findAll('.provider')[0].find('button').attributes('aria-pressed')).toBe('false');
    providers.value.push(runtime('new-provider', false));
    await flushPromises();
    expect(names()).toEqual([...original, 'new-provider']);
    wrapper.unmount();
  });
});
