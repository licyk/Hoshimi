import { flushPromises, mount } from '@vue/test-utils';
import { createPinia } from 'pinia';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import LoginDialog from '@/components/LoginDialog.vue';

const state = vi.hoisted(() => ({ start: vi.fn(), poll: vi.fn(), cancel: vi.fn(), done: vi.fn() }));
vi.mock('@/api/queries/channels', () => ({
  startLogin: state.start, pollLogin: state.poll, cancelLogin: state.cancel, submitLoginInput: vi.fn(), useLoginDone: () => state.done,
}));
vi.mock('@/ui', () => ({
  icons: {},
  AppDialog: { props: ['open'], template: '<div v-if="open"><slot /><slot name="actions" /></div>' },
  AppButton: { emits: ['click'], template: '<button @click="$emit(\'click\')"><slot /></button>' },
  Stepper: { template: '<div />' }, IconButton: { template: '<button />' }, TextField: { template: '<input />' },
  ErrorNotice: { props: ['message'], template: '<p role="alert">{{ message }}<slot name="actions" /></p>' },
  useSnackbar: () => ({ show: vi.fn(), error: vi.fn() }),
}));

describe('account re-login', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    state.start.mockResolvedValue({ id: 'attempt', status: 'pending' });
    state.cancel.mockResolvedValue({ cancelled: true });
    vi.spyOn(window, 'open').mockImplementation(() => null);
  });
  afterEach(() => vi.restoreAllMocks());

  it.each(['succeeded', 'failed'])('targets the selected account and refreshes only on success (%s)', async (status) => {
    state.poll.mockResolvedValue({ id: 'attempt', status, account_id: 'mine', message: status === 'failed' ? 'Denied' : '' });
    const w = mount(LoginDialog, { props: { open: false, provider: 'openai', name: 'OpenAI', accountId: 'mine' }, global: { plugins: [createPinia()] } });
    await w.setProps({ open: true });
    await flushPromises();
    expect(state.start).toHaveBeenCalledWith('openai', 'mine');
    expect(state.done).toHaveBeenCalledTimes(status === 'succeeded' ? 1 : 0);
    if (status === 'failed') expect(w.find('[role="alert"]').text()).toContain('Denied');
    w.unmount();
  });

  it('cancels a pending re-login on close without refreshing the account', async () => {
    state.poll.mockResolvedValue({ id: 'attempt', status: 'pending', login_url: 'https://login.example' });
    const w = mount(LoginDialog, { props: { open: false, provider: 'openai', name: 'OpenAI', accountId: 'mine' }, global: { plugins: [createPinia()] } });
    await w.setProps({ open: true });
    await flushPromises();
    await w.setProps({ open: false });
    await flushPromises();
    expect(state.cancel).toHaveBeenCalledWith('attempt');
    expect(state.done).not.toHaveBeenCalled();
    w.unmount();
  });
});
