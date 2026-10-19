import { mount, flushPromises } from '@vue/test-utils';
import { createPinia } from 'pinia';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import ChannelsView from '@/views/ChannelsView.vue';

const state = vi.hoisted(() => ({ provider: 'nvidia', add: vi.fn(), cancel: vi.fn(), submit: vi.fn(), start: vi.fn(), poll: vi.fn(), done: vi.fn() }));

vi.mock('@/api/queries/app', () => ({
  useProviders: () => ({ data: { value: [{ id: state.provider, display_name: state.provider, keyless: state.provider === 'kilo', account_ids: [], enabled: true }] }, isPending: { value: false }, isFetching: { value: false } }),
  useSettings: () => ({ data: { value: { server: {} } } }),
  useUpdateSettings: () => ({ mutate: vi.fn() }),
}));
vi.mock('@/api/queries/channels', () => {
  const query = () => ({ data: { value: {} } });
  const mutation = () => ({ mutate: vi.fn(), isPending: { value: false } });
  return {
    useStations: () => ({ data: { value: [] } }),
    useProviderForms: () => ({ data: { value: [{ id: state.provider, forms: state.provider === 'ollama'
      ? [{ method: 'local', defaults: { base_url: 'http://localhost:11434/v1' }, fields: [{ key: 'base_url', kind: 'text', required: false }, { key: 'api_key', kind: 'secret', required: false }] }]
      : [{ method: 'api_key', defaults: {}, fields: [{ key: 'api_key', kind: 'secret', required: true }] }] }] } }),
    useLoginProviders: () => ({ data: { value: ['kilo'] } }),
    useBalances: query, useRefreshStatus: query, useDeleteAccount: mutation, useClaimCredits: mutation, useRefreshNow: mutation,
    useLoginDone: () => state.done,
    useAddAccount: () => ({ mutate: state.add, isPending: { value: false } }),
    startLogin: state.start, pollLogin: state.poll, cancelLogin: state.cancel, submitLoginInput: state.submit,
  };
});
vi.mock('@/ui', () => {
  const slot = { template: '<div><slot /></div>' };
  return {
    icons: {}, Surface: slot, Badge: slot, Skeleton: slot, Switch: slot, ConfirmDialog: slot, EmptyState: slot, IconButton: slot,
    AppButton: { props: ['disabled'], emits: ['click'], template: '<button :disabled="disabled" @click="$emit(\'click\')"><slot /></button>' },
    TextField: { props: ['label', 'type', 'modelValue'], template: '<label>{{ label }}<input :type="type" :value="modelValue" @input="$emit(\'update:modelValue\', $event.target.value)" /></label>' },
    SelectField: slot,
    AppDialog: { props: ['open'], template: '<div v-if="open"><slot /><slot name="actions" /></div>' },
    useSnackbar: () => ({ show: vi.fn(), error: vi.fn() }),
  };
});

describe('channel credential and login flows', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    state.provider = 'nvidia';
    state.cancel.mockResolvedValue({ cancelled: true });
    state.submit.mockResolvedValue({ accepted: true });
    vi.spyOn(window, 'open').mockReturnValue(null);
  });
  afterEach(() => vi.useRealTimers());
  it('adds a NVIDIA key without demanding an aggregator URL', async () => {
    const wrapper = mount(ChannelsView, { global: { plugins: [createPinia()] } });
    await wrapper.findAll('button').find((button) => button.text() === 'Add account')!.trigger('click');
    expect(wrapper.text()).not.toContain('Base URL');
    await wrapper.find('input[type="password"]').setValue('secret');
    await wrapper.findAll('button').find((button) => button.text() === 'Save account')!.trigger('click');
    expect(state.add.mock.calls[0][0]).toEqual({ provider: 'nvidia', account: { api_key: 'secret' } });
    wrapper.unmount();
  });
  it('adds a local Ollama server without requiring a key', async () => {
    state.provider = 'ollama';
    const wrapper = mount(ChannelsView, { global: { plugins: [createPinia()] } });
    await wrapper.findAll('button').find((button) => button.text() === 'Add account')!.trigger('click');
    await flushPromises();
    await wrapper.findAll('button').find((button) => button.text() === 'Save account')!.trigger('click');
    expect(state.add.mock.calls[0][0]).toEqual({ provider: 'ollama', account: { base_url: 'http://localhost:11434/v1' } });
    wrapper.unmount();
  });
  it('shows Kilo logged out, displays device codes, accepts manual input and cancels', async () => {
    state.provider = 'kilo';
    const session = { id: 'session', provider: 'kilo', status: 'pending', login_url: 'https://login.example', user_code: 'ABCD', waiting_for: 'manual_code' };
    state.start.mockResolvedValue(session);
    state.poll.mockResolvedValue(session);
    const wrapper = mount(ChannelsView, { global: { plugins: [createPinia()] } });
    await wrapper.findAll('button').find((button) => button.text() === 'Sign in')!.trigger('click');
    await flushPromises();
    expect(wrapper.text()).toContain('ABCD');
    await wrapper.find('input[type="password"]').setValue('code#state');
    await wrapper.findAll('button').find((button) => button.text() === 'Submit code')!.trigger('click');
    await flushPromises();
    expect(state.submit).toHaveBeenCalledWith('session', 'code#state');
    await wrapper.findAll('button').find((button) => button.text() === 'Close')!.trigger('click');
    expect(state.cancel).toHaveBeenCalledWith('session');
    wrapper.unmount();
  });
  it('cancels a session that starts after its dialog has closed', async () => {
    state.provider = 'kilo';
    let resolve!: (value: unknown) => void;
    state.start.mockReturnValue(new Promise((done) => { resolve = done; }));
    const wrapper = mount(ChannelsView, { global: { plugins: [createPinia()] } });
    await wrapper.findAll('button').find((button) => button.text() === 'Sign in')!.trigger('click');
    await wrapper.findAll('button').find((button) => button.text() === 'Close')!.trigger('click');
    resolve({ id: 'late-session', status: 'pending' });
    await flushPromises();
    expect(state.cancel).toHaveBeenCalledWith('late-session');
    expect(state.poll).not.toHaveBeenCalled();
    wrapper.unmount();
  });

  it('closes after a successful login and refreshes account data without cancelling it', async () => {
    vi.useFakeTimers();
    state.provider = 'kilo';
    const session = { id: 'session', provider: 'kilo', status: 'pending' };
    state.start.mockResolvedValue(session);
    state.poll.mockResolvedValueOnce(session).mockResolvedValue({ ...session, status: 'succeeded', account_id: 'kilo-account' });
    const wrapper = mount(ChannelsView, { global: { plugins: [createPinia()] } });
    await wrapper.findAll('button').find((button) => button.text() === 'Sign in')!.trigger('click');
    await flushPromises();
    expect(wrapper.find('.login-body').exists()).toBe(true);
    await vi.advanceTimersByTimeAsync(1500);
    await flushPromises();
    expect(wrapper.find('.login-body').exists()).toBe(false);
    expect(state.done).toHaveBeenCalledOnce();
    expect(state.cancel).not.toHaveBeenCalled();
    const calls = state.poll.mock.calls.length;
    await vi.advanceTimersByTimeAsync(3000);
    expect(state.poll).toHaveBeenCalledTimes(calls);
    wrapper.unmount();
  });

  it.each(['failed', 'cancelled', 'expired'])('keeps the dialog open when login is %s', async (status) => {
    state.provider = 'kilo';
    state.start.mockResolvedValue({ id: 'session', provider: 'kilo', status: 'pending' });
    state.poll.mockResolvedValue({ id: 'session', provider: 'kilo', status, message: 'Login did not finish' });
    const wrapper = mount(ChannelsView, { global: { plugins: [createPinia()] } });
    await wrapper.findAll('button').find((button) => button.text() === 'Sign in')!.trigger('click');
    await flushPromises();
    expect(wrapper.find('.login-body').text()).toContain('Login did not finish');
    expect(state.done).not.toHaveBeenCalled();
    wrapper.unmount();
  });

  it('keeps the dialog open if starting login or polling fails', async () => {
    state.provider = 'kilo';
    state.start.mockRejectedValueOnce(new Error('Cannot start login'));
    const wrapper = mount(ChannelsView, { global: { plugins: [createPinia()] } });
    const signIn = () => wrapper.findAll('button').find((button) => button.text() === 'Sign in')!.trigger('click');
    await signIn();
    await flushPromises();
    expect(wrapper.find('.login-body').text()).toContain('Cannot start login');
    await wrapper.findAll('button').find((button) => button.text() === 'Close')!.trigger('click');
    state.start.mockResolvedValue({ id: 'session', provider: 'kilo', status: 'pending' });
    state.poll.mockRejectedValueOnce(new Error('Network unavailable'));
    await signIn();
    await flushPromises();
    expect(wrapper.find('.login-body').exists()).toBe(true);
    expect(state.done).not.toHaveBeenCalled();
    wrapper.unmount();
  });
});
