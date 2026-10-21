import { flushPromises, mount } from '@vue/test-utils';
import { createPinia } from 'pinia';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import AddAccountDialog from '@/components/AddAccountDialog.vue';

const state = vi.hoisted(() => ({ provider: 'nvidia', add: vi.fn() }));

vi.mock('@/api/queries/channels', () => ({
  useStations: () => ({ data: { value: [{ id: 'openrouter', display_name: 'OpenRouter', base_url: 'https://openrouter.ai/api/v1', key_env: 'OPENROUTER_API_KEY' }] }, isPending: { value: false } }),
  useProviderForms: () => ({
    data: {
      value: [
        { id: 'nvidia', forms: [{ method: 'api_key', defaults: {}, fields: [{ key: 'api_key', kind: 'secret', required: true }] }] },
        { id: 'ollama', forms: [{ method: 'local', defaults: { base_url: 'http://localhost:11434/v1' }, fields: [{ key: 'base_url', kind: 'text', required: false }, { key: 'api_key', kind: 'secret', required: false }] }] },
      ],
    },
  }),
  useAddAccount: () => ({ mutate: state.add, isPending: { value: false } }),
}));
vi.mock('@/ui', () => {
  const slot = { template: '<div><slot /></div>' };
  return {
    icons: {},
    SelectField: slot,
    SegmentedButton: slot,
    PickerMenu: { props: ['modelValue', 'options'], emits: ['update:modelValue'], template: '<select class="picker" :value="modelValue" @change="$emit(\'update:modelValue\', $event.target.value)"><option v-for="o in options" :key="o.value" :value="o.value">{{ o.label }}</option></select>' },
    AppButton: { props: ['disabled'], emits: ['click'], template: '<button :disabled="disabled" @click="$emit(\'click\')"><slot /></button>' },
    TextField: { props: ['label', 'type', 'modelValue'], template: '<label>{{ label }}<input :type="type" :value="modelValue" @input="$emit(\'update:modelValue\', $event.target.value)" /></label>' },
    AppDialog: { props: ['open', 'title'], template: '<div v-if="open"><h2>{{ title }}</h2><slot /><slot name="actions" /></div>' },
    useSnackbar: () => ({ show: vi.fn(), error: vi.fn() }),
  };
});

const open = (provider: string) => mount(AddAccountDialog, { props: { provider, name: provider, open: false }, global: { plugins: [createPinia()] } });
const save = (w: ReturnType<typeof open>) => w.findAll('button').find((b) => b.text() === 'Save account')!;

describe('adding an account', () => {
  beforeEach(() => vi.clearAllMocks());

  it('adds a NVIDIA key without demanding an aggregator URL', async () => {
    const w = open('nvidia');
    await w.setProps({ open: true });
    expect(w.text()).toContain('Add an account to nvidia');
    expect(w.text()).not.toContain('Base URL');
    expect(save(w).attributes('disabled')).toBeDefined();
    await w.find('input[type="password"]').setValue('secret');
    await save(w).trigger('click');
    expect(state.add.mock.calls[0][0]).toEqual({ provider: 'nvidia', account: { api_key: 'secret' } });
  });

  it('adds a local Ollama server without requiring a key', async () => {
    const w = open('ollama');
    await w.setProps({ open: true });
    await flushPromises();
    await save(w).trigger('click');
    expect(state.add.mock.calls[0][0]).toEqual({ provider: 'ollama', account: { base_url: 'http://localhost:11434/v1' } });
  });

  it('names a listed station instead of an address for the aggregator', async () => {
    const w = open('agg');
    await w.setProps({ open: true });
    expect(w.text()).toContain('Base URL');
    await w.find('select.picker').setValue('openrouter');
    expect(w.text()).not.toContain('Base URL');
    await w.find('input[type="password"]').setValue('key');
    await save(w).trigger('click');
    expect(state.add.mock.calls[0][0]).toEqual({ provider: 'agg', account: { api_key: 'key', preset: 'openrouter' } });
  });
});
