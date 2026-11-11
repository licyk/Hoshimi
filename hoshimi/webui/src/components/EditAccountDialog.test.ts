import { flushPromises, mount } from '@vue/test-utils';
import { createPinia } from 'pinia';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import EditAccountDialog from '@/components/EditAccountDialog.vue';

const state = vi.hoisted(() => ({ get: vi.fn(), mutate: vi.fn(), reset: vi.fn() }));
vi.mock('@/api/queries/channels', () => ({
  getAccount: state.get,
  useUpdateAccount: () => ({ mutate: state.mutate, reset: state.reset, isPending: { value: false } }),
}));
vi.mock('@/ui', () => ({
  icons: {},
  AppDialog: { props: ['open', 'title'], template: '<div v-if="open"><h1>{{ title }}</h1><slot /><slot name="actions" /></div>' },
  AppButton: { props: ['disabled', 'loading'], emits: ['click'], template: '<button :disabled="disabled || loading" @click="$emit(\'click\')"><slot /></button>' },
  IconButton: { props: ['label'], emits: ['click'], template: '<button :aria-label="label" @click="$emit(\'click\')" />' },
  TextField: { props: ['modelValue', 'type', 'label'], emits: ['update:modelValue'], template: '<input :aria-label="label" :type="type" :value="modelValue" @input="$emit(\'update:modelValue\', $event.target.value)" />' },
  SelectField: { template: '<select />' },
  ErrorNotice: { props: ['message'], template: '<p role="alert">{{ message }}<slot name="actions" /></p>' },
  useSnackbar: () => ({ show: vi.fn() }),
}));

const detail = () => ({
  account: { id: 'mine', api_key: 'actual-token', base_url: 'https://old.test/v1', models: ['model-a', 'model-b'] },
  form: { method: 'api_key', defaults: {}, fields: [
    { key: 'api_key', kind: 'secret', required: true }, { key: 'base_url', kind: 'text', required: false }, { key: 'models', kind: 'list', required: false },
  ] },
});
const mountEditor = (open = true) => mount(EditAccountDialog, { props: { open, provider: 'openai', accountId: 'mine' }, global: { plugins: [createPinia()] } });

describe('editing a channel account', () => {
  beforeEach(() => { vi.clearAllMocks(); state.get.mockResolvedValue(detail()); });

  it('fetches only when opened, prefills real values and masks secrets until revealed', async () => {
    const w = mountEditor(false);
    expect(state.get).not.toHaveBeenCalled();
    await w.setProps({ open: true });
    await flushPromises();
    expect(state.get).toHaveBeenCalledWith('openai', 'mine');
    const secret = w.findAll('input')[0]!;
    expect((secret.element as HTMLInputElement).value).toBe('actual-token');
    expect(secret.attributes('type')).toBe('password');
    await w.find('button[aria-label]').trigger('click');
    expect(secret.attributes('type')).toBe('text');
    await w.find('button[aria-label]').trigger('click');
    expect(secret.attributes('type')).toBe('password');
    await w.setProps({ open: false });
    await w.setProps({ open: true });
    await flushPromises();
    expect(w.find('input').attributes('type')).toBe('password');
    w.unmount();
  });

  it('saves the selected ID with changed fields and clears an optional value', async () => {
    const w = mountEditor();
    await flushPromises();
    await w.findAll('input')[0]!.setValue('new-token');
    await w.findAll('input')[1]!.setValue('');
    await w.find('form').trigger('submit');
    expect(state.mutate.mock.calls[0]![0]).toEqual({ provider: 'openai', id: 'mine', account: { api_key: 'new-token', models: ['model-a', 'model-b'] } });
    state.mutate.mock.calls[0]![1].onError(new Error('Save failed'));
    await flushPromises();
    expect(w.find('[role="alert"]').text()).toContain('Save failed');
    expect(w.emitted('update:open')).toBeUndefined();
    expect((w.find('input').element as HTMLInputElement).value).toBe('new-token');
    state.mutate.mock.calls[0]![1].onSuccess();
    expect(w.emitted('update:open')).toEqual([[false]]);
    w.unmount();
  });

  it('ignores old account responses after the selection changes or the dialog closes', async () => {
    let resolve!: (value: ReturnType<typeof detail>) => void;
    state.get.mockImplementationOnce(() => new Promise((done) => { resolve = done; }));
    const w = mountEditor();
    await w.setProps({ accountId: 'other' });
    await flushPromises();
    const old = detail(); old.account.api_key = 'stale-token';
    resolve(old);
    await flushPromises();
    expect((w.find('input').element as HTMLInputElement).value).toBe('actual-token');
    await w.setProps({ open: false });
    expect(w.findAll('input')).toHaveLength(0);
    expect(state.mutate).not.toHaveBeenCalled();
    w.unmount();
  });
});
