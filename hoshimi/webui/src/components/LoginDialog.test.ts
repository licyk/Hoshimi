import { flushPromises, mount } from '@vue/test-utils';
import { createPinia } from 'pinia';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import LoginDialog from '@/components/LoginDialog.vue';

const state = vi.hoisted(() => ({ cancel: vi.fn(), submit: vi.fn(), start: vi.fn(), poll: vi.fn(), done: vi.fn() }));

vi.mock('@/api/queries/channels', () => ({
  startLogin: state.start,
  pollLogin: state.poll,
  cancelLogin: state.cancel,
  submitLoginInput: state.submit,
  useLoginDone: () => state.done,
}));
vi.mock('@/ui', () => {
  const slot = { template: '<div><slot /></div>' };
  return {
    icons: {},
    IconButton: slot,
    Stepper: { props: ['steps'], template: '<ol class="steps"><li v-for="s in steps" :key="s.id" :data-state="s.state">{{ s.label }}</li></ol>' },
    AppButton: { props: ['disabled'], emits: ['click'], template: '<button :disabled="disabled" @click="$emit(\'click\')"><slot /></button>' },
    TextField: { props: ['label', 'type', 'modelValue'], template: '<label>{{ label }}<input :type="type" :value="modelValue" @input="$emit(\'update:modelValue\', $event.target.value)" /></label>' },
    AppDialog: { props: ['open'], template: '<div v-if="open" class="dialog"><slot /><slot name="actions" /></div>' },
    useSnackbar: () => ({ show: vi.fn(), error: vi.fn() }),
  };
});

const dialog = () => mount(LoginDialog, { props: { provider: 'kilo', name: 'Kilo', open: false, 'onUpdate:open': (v: boolean) => w.setProps({ open: v }) }, global: { plugins: [createPinia()] } });
let w: ReturnType<typeof dialog>;
const button = (text: string) => w.findAll('button').find((b) => b.text() === text)!;
const stepState = (i: number) => w.findAll('.steps li')[i].attributes('data-state');

describe('signing a channel in', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    state.cancel.mockResolvedValue({ cancelled: true });
    state.submit.mockResolvedValue({ accepted: true });
    vi.spyOn(window, 'open').mockReturnValue(null);
  });
  afterEach(() => {
    w?.unmount();
    vi.useRealTimers();
  });

  it('shows the device code, accepts a pasted code and cancels on close', async () => {
    const session = { id: 'session', provider: 'kilo', status: 'pending', login_url: 'https://login.example', user_code: 'ABCD', waiting_for: 'manual_code' };
    state.start.mockResolvedValue(session);
    state.poll.mockResolvedValue(session);
    w = dialog();
    await w.setProps({ open: true });
    await flushPromises();
    expect(w.text()).toContain('ABCD');
    expect(window.open).toHaveBeenCalledWith('https://login.example', '_blank', 'noopener');
    expect([stepState(0), stepState(1)]).toEqual(['done', 'running']);
    await w.find('input[type="password"]').setValue('code#state');
    await button('Submit code').trigger('click');
    await flushPromises();
    expect(state.submit).toHaveBeenCalledWith('session', 'code#state');
    await button('Cancel').trigger('click');
    expect(state.cancel).toHaveBeenCalledWith('session');
  });

  it('cancels a session that starts after its dialog has closed', async () => {
    let resolve!: (value: unknown) => void;
    state.start.mockReturnValue(new Promise((done) => (resolve = done)));
    w = dialog();
    await w.setProps({ open: true });
    await button('Cancel').trigger('click');
    resolve({ id: 'late-session', status: 'pending' });
    await flushPromises();
    expect(state.cancel).toHaveBeenCalledWith('late-session');
    expect(state.poll).not.toHaveBeenCalled();
  });

  it('marks the login done, refreshes the account data and stops polling without cancelling', async () => {
    vi.useFakeTimers();
    const session = { id: 'session', provider: 'kilo', status: 'pending' };
    state.start.mockResolvedValue(session);
    state.poll.mockResolvedValueOnce(session).mockResolvedValue({ ...session, status: 'succeeded', account_id: 'kilo-account' });
    w = dialog();
    await w.setProps({ open: true });
    await flushPromises();
    await vi.advanceTimersByTimeAsync(1500);
    await flushPromises();
    expect(stepState(2)).toBe('done');
    expect(w.text()).toContain('Signed in as kilo-account.');
    expect(state.done).toHaveBeenCalledOnce();
    const calls = state.poll.mock.calls.length;
    await vi.advanceTimersByTimeAsync(3000);
    expect(state.poll).toHaveBeenCalledTimes(calls);
    await button('Done').trigger('click');
    expect(state.cancel).not.toHaveBeenCalled();
  });

  it.each(['failed', 'cancelled', 'expired'])('keeps the dialog open with the reason when login is %s', async (status) => {
    state.start.mockResolvedValue({ id: 'session', provider: 'kilo', status: 'pending' });
    state.poll.mockResolvedValue({ id: 'session', provider: 'kilo', status, message: 'Login did not finish' });
    w = dialog();
    await w.setProps({ open: true });
    await flushPromises();
    expect(w.find('.dialog').text()).toContain('Login did not finish');
    expect(stepState(1)).toBe('failed');
    expect(state.done).not.toHaveBeenCalled();
  });

  it('offers a retry when starting fails, and a lost poll is not a verdict', async () => {
    state.start.mockRejectedValueOnce(new Error('Cannot start login'));
    w = dialog();
    await w.setProps({ open: true });
    await flushPromises();
    expect(w.find('.dialog').text()).toContain('Cannot start login');
    state.start.mockResolvedValue({ id: 'session', provider: 'kilo', status: 'pending' });
    state.poll.mockRejectedValueOnce(new Error('Network unavailable'));
    await button('Retry').trigger('click');
    await flushPromises();
    expect(w.find('.dialog').text()).not.toContain('Network unavailable');
    expect(state.done).not.toHaveBeenCalled();
  });
});
