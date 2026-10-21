import { flushPromises, mount } from '@vue/test-utils';
import { createPinia } from 'pinia';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { reactive, ref } from 'vue';
import SettingsView from '@/views/SettingsView.vue';

const state = vi.hoisted(() => ({ update: vi.fn() }));
const route = reactive({ query: {} as Record<string, string> });
const settings = ref({
  data_dir: '/data', config_path: '/data/config.json', restart_required: false, network: { mode: 'auto', proxy_configured: false, system_proxy: false }, providers: [],
  server: { host: '127.0.0.1', port: 10600, allow_cidrs: ['10.0.0.0/8'], allow_hosts: [], allow_origins: [], auto_checkin: true, auto_refresh: true, default_model: null, max_account_retries: 3, max_body_bytes: 1000, access_token_configured: false, model_api_key_configured: false },
});

vi.mock('vue-router', () => ({ useRoute: () => route, useRouter: () => ({ replace: ({ query }: { query: Record<string, string> }) => (route.query = query), push: vi.fn() }) }));
vi.mock('@/api/queries/app', () => ({
  useSettings: () => ({ data: settings }),
  useModels: () => ({ data: { value: { data: [] } }, isPending: { value: false } }),
  useMeta: () => ({ data: { value: { auth_required: true } } }),
  useVersion: () => ({ data: { value: { version: 'test' } } }),
  useUpdateSettings: () => ({ mutate: state.update }),
}));
vi.mock('@/api/queries/channels', () => ({ useChannels: () => ({ data: { value: undefined } }) }));
vi.mock('@/components/NetworkSettings.vue', () => ({ default: { template: '<div class="network" />' } }));
vi.mock('@/ui', () => {
  const slot = { template: '<div><slot /></div>' };
  return {
    icons: {}, TRANSITIONS: {}, AppButton: slot, AppIcon: slot, DataList: slot, PickerMenu: slot, SegmentedButton: slot, SelectField: slot, Skeleton: slot, TextField: slot, TokenField: slot,
    AxisPanes: slot,
    PageSection: { props: ['title'], template: '<section><h2>{{ title }}</h2><slot /></section>' },
    Tabs: { props: ['tabs', 'modelValue'], emits: ['update:modelValue'], template: '<nav><button v-for="t in tabs" :key="t.value" class="tab" @click="$emit(\'update:modelValue\', t.value)">{{ t.label }}</button></nav>' },
    ChipInput: { props: ['label', 'values'], emits: ['change'], template: '<div class="chips" :data-label="label"><button class="add" @click="$emit(\'change\', [...values, \'192.168.0.0/16\'])">{{ values.join(\' \') }}</button></div>' },
    Switch: { props: ['label', 'modelValue'], emits: ['update:modelValue'], template: '<button class="switch" :aria-pressed="modelValue" @click="$emit(\'update:modelValue\', !modelValue)">{{ label }}</button>' },
    useSnackbar: () => ({ show: vi.fn(), error: vi.fn() }),
  };
});

describe('settings', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    route.query = {};
    settings.value.restart_required = false;
  });

  it('shows one tab at a time and keeps the tab in the address', async () => {
    const w = mount(SettingsView, { global: { plugins: [createPinia()] } });
    expect(w.findAll('h2').map((h) => h.text())).toEqual(['General', 'Requests']);
    await w.findAll('.tab').find((t) => t.text() === 'Security')!.trigger('click');
    expect(route.query.tab).toBe('security');
    expect(w.findAll('h2').map((h) => h.text())).toEqual(['Security']);
  });

  it('saves an allow-list as a list the moment it changes', async () => {
    route.query = { tab: 'security' };
    const w = mount(SettingsView, { global: { plugins: [createPinia()] } });
    await w.find('.chips .add').trigger('click');
    expect(state.update.mock.calls[0][0]).toEqual({ security: { allow_cidrs: ['10.0.0.0/8', '192.168.0.0/16'] } });
  });

  it('keeps the automation timers with the other server settings', async () => {
    route.query = { tab: 'automation' };
    const w = mount(SettingsView, { global: { plugins: [createPinia()] } });
    await w.findAll('.switch').find((s) => s.text() === 'Daily check-in')!.trigger('click');
    expect(state.update.mock.calls[0][0]).toEqual({ server: { auto_checkin: false } });
  });

  it('says when the saved address waits for a restart', async () => {
    settings.value.restart_required = true;
    const w = mount(SettingsView, { global: { plugins: [createPinia()] } });
    await flushPromises();
    expect(w.text()).toContain('127.0.0.1:10600');
  });
});
