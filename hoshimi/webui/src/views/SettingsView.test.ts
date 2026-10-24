import { flushPromises, mount } from '@vue/test-utils';
import { createPinia } from 'pinia';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { reactive, ref } from 'vue';
import SettingsView from '@/views/SettingsView.vue';

const state = vi.hoisted(() => ({ update: vi.fn() }));
const route = reactive({ query: {} as Record<string, string> });
const settings = ref({
  data_dir: '/data', config_path: '/data/config.json', restart_required: false, network: { mode: 'auto', proxy_configured: false, system_proxy: false }, providers: [],
  server: { host: '127.0.0.1', port: 10600, allow_cidrs: ['10.0.0.0/8'], allow_hosts: [], allow_origins: [], auto_checkin: true, auto_refresh: true, default_model: null, max_account_retries: 3, max_body_bytes: 1000, access_token_configured: false, model_api_key_configured: false, strict_port: false, stats_keep_days: 90 },
  listening: { host: '127.0.0.1', port: 10601, requested_port: 10600, fallback: true },
  playground: { system_prompt: '', title_model: '', vision_model: 'auto', tools: ['read'], workspace: '', workspace_path: '/data/workspace', approval: 'review', review_model: '', shell: 'auto', shell_in_effect: 'bash', shells: ['bash', 'sh'], search_url: '', auto_compact: true, compact_threshold: 0.85, compact_model: '' },
});

vi.mock('vue-router', () => ({ useRoute: () => route, useRouter: () => ({ replace: ({ query }: { query: Record<string, string> }) => (route.query = query), push: vi.fn() }) }));
vi.mock('@/api/queries/app', () => ({
  useSettings: () => ({ data: settings }),
  useModels: () => ({ data: { value: { data: [] } }, isPending: { value: false } }),
  useModelSpecs: () => ({ data: { value: { data: [] } } }),
  useMeta: () => ({ data: { value: { auth_required: true } } }),
  useVersion: () => ({ data: { value: { version: 'test' } } }),
  useUpdateSettings: () => ({ mutate: state.update }),
}));
vi.mock('@/api/queries/channels', () => ({ useChannels: () => ({ data: { value: undefined } }) }));
vi.mock('@/components/NetworkSettings.vue', () => ({ default: { template: '<div class="network" />' } }));
vi.mock('@/ui', () => {
  const slot = { template: '<div><slot /></div>' };
  return {
    icons: {}, TRANSITIONS: {}, AppButton: slot, AppIcon: slot, DataList: slot, NumberField: slot,
    PickerMenu: { props: ['label', 'modelValue', 'options'], emits: ['update:modelValue'], template: '<select class="picker" :aria-label="label" :value="modelValue" @change="$emit(\'update:modelValue\', $event.target.value)"><option v-for="o in options" :key="o.value" :value="o.value">{{ o.label }}</option></select>' }, SegmentedButton: slot, SelectField: slot, Skeleton: slot, TokenField: slot,
    TextField: { props: ['label', 'modelValue'], emits: ['change'], template: '<input class="text-field" :aria-label="label" :value="modelValue" @change="$emit(\'change\', $event.target.value)" />' },
    AxisPanes: slot,
    Collapse: { props: ['show'], template: '<div v-if="show"><slot /></div>' },
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
    expect(w.findAll('h2').map((h) => h.text())).toEqual(['General', 'Requests', 'Usage history', 'Playground', 'Context']);
    await w.findAll('.tab').find((t) => t.text() === 'Security')!.trigger('click');
    expect(route.query.tab).toBe('security');
    expect(w.findAll('h2').map((h) => h.text())).toEqual(['Security']);
  });

  it('saves the Playground system prompt with the other general settings', async () => {
    const w = mount(SettingsView, { global: { plugins: [createPinia()] } });
    const prompt = w.find('input[aria-label="System prompt"]');
    await prompt.setValue('Answer briefly.');
    await prompt.trigger('change');
    expect(state.update.mock.calls[0][0]).toEqual({ playground: { system_prompt: 'Answer briefly.' } });
  });

  it('turns tools on one by one, in their order, and sets when they ask first', async () => {
    route.query = { tab: 'tools' };
    const w = mount(SettingsView, { global: { plugins: [createPinia()] } });
    expect(w.findAll('h2').map((h) => h.text())).toEqual(['Tools for new chats', 'How tools run']);
    const toggle = (label: string) => w.findAll('.switch').find((s) => s.text() === label)!.trigger('click');
    await toggle('Run commands');
    await toggle('Read files');
    const mode = w.find('select[aria-label="When tools ask first"]');
    expect(mode.findAll('option').map((o) => o.text())).toEqual(['Automatic review', 'Ask first', 'Ask outside the workspace', 'Full access']);
    await mode.setValue('full');
    // The shell commands run in: automatic (and what it comes to here), or one of those installed.
    const shell = w.find('select[aria-label="Shell"]');
    expect(shell.findAll('option').map((o) => o.text())).toEqual(['Automatic', 'bash', 'sh']);
    await shell.setValue('sh');
    expect(state.update.mock.calls.map((c) => c[0])).toEqual([
      { playground: { tools: ['read', 'shell'] } },
      { playground: { tools: [] } },
      { playground: { approval: 'full' } },
      { playground: { shell: 'sh' } },
    ]);
    const workspace = w.find('input[aria-label="Workspace"]');
    await workspace.setValue('~/agent');
    await workspace.trigger('change');
    expect(state.update.mock.calls.at(-1)![0]).toEqual({ playground: { workspace: '~/agent' } });
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

  it('says where the server listens, why it is not the saved port, and saves strict port mode', async () => {
    route.query = { tab: 'access' };
    const w = mount(SettingsView, { global: { plugins: [createPinia()] } });
    expect(w.find('.listening').text()).toContain('127.0.0.1:10601');
    expect(w.find('.listening').text()).toContain('10600');
    await w.findAll('.switch').find((s) => s.text() === 'Use only this port')!.trigger('click');
    expect(state.update.mock.calls[0][0]).toEqual({ server: { strict_port: true } });
  });

  it('says when the saved address waits for a restart', async () => {
    settings.value.restart_required = true;
    const w = mount(SettingsView, { global: { plugins: [createPinia()] } });
    await flushPromises();
    expect(w.text()).toContain('127.0.0.1:10600');
  });
});
