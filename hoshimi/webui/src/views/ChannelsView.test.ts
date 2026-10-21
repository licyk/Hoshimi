import { flushPromises, mount } from '@vue/test-utils';
import { createPinia } from 'pinia';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { ref } from 'vue';
import ChannelsView from '@/views/ChannelsView.vue';

const zero = { input: 0, output: 0, reasoning: 0, cache_read: 0, cache_write: 0, calls: 0, failed: 0, no_usage: 0 };
const channel = (id: string, status: string, accounts = 1) => ({
  id, display_name: id, status, enabled: status !== 'disabled', keyless: false, configured: true, requires_authorization: false, loginable: false, addable: true,
  forms: [], models: 0, options: [], network: { mode: 'inherit', proxy_configured: false, system_proxy: false }, client_profile: null, diagnostics: [], quota: null,
  accounts: Array.from({ length: accounts }, (_, i) => ({ id: `${id}-${i}`, needs_login: false, refreshable: false, expires_at: null, failures: 0, last_error: null, last_ok: null, cooled_reason: '', cooled_seconds: 0, usage_today: zero, last_used_at: null })),
  usage_today: zero,
});
const report = ref({ channels: [channel('zeta', 'ok'), channel('alpha', 'attention'), channel('beta', 'disabled')], checkin: { last_date: '', last_result: '', last_at: 0, auto_checkin: true }, auto_refresh: true, auto_checkin: true });
const replace = vi.fn();

vi.mock('vue-router', () => ({ useRouter: () => ({ replace, push: vi.fn() }), RouterLink: { props: ['to'], template: '<slot :href="to" :navigate="() => {}" />' } }));
vi.mock('@/api/queries/channels', () => ({
  useChannels: () => ({ data: report, isPending: { value: false } }),
  useBalances: () => ({ data: { value: undefined } }),
  useClaimCredits: () => ({ mutate: vi.fn(), isPending: { value: false } }),
  useRefreshNow: () => ({ mutate: vi.fn(), isPending: { value: false } }),
}));
vi.mock('@/components/ChannelDetail.vue', () => ({ default: { props: ['channel'], template: '<section class="detail">{{ channel.id }}</section>' } }));
vi.mock('@/ui', () => {
  const slot = { template: '<div><slot /></div>' };
  return {
    icons: {}, AppButton: slot, IconButton: slot, Badge: { props: ['value'], template: '<span class="badge">{{ value }}</span>' }, Meter: slot, Skeleton: slot, SearchField: slot,
    Toolbar: { template: '<div><slot /><slot name="end" /></div>' },
    EmptyState: { props: ['title'], template: '<p class="empty">{{ title }}</p>' },
    Chip: { props: ['label', 'count', 'selected'], emits: ['click'], template: '<button class="chip" :aria-pressed="selected" @click="$emit(\'click\')">{{ label }} {{ count }}</button>' },
    ListDetail: { props: ['detail'], template: '<div><slot name="list" /><slot v-if="detail" name="detail" :wide="true" /><slot v-else name="placeholder" /></div>' },
    TRANSITIONS: { list: 'list' }, keepLeavingInPlace: {},
    useSnackbar: () => ({ show: vi.fn(), error: vi.fn() }),
  };
});

const names = (w: ReturnType<typeof mount>) => w.findAll('.row .name').map((n) => n.text());

describe('the channels list', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    report.value.channels = [channel('zeta', 'ok'), channel('alpha', 'attention'), channel('beta', 'disabled')];
  });

  it('keeps rows where they were when saving moves a channel in the server order', async () => {
    const w = mount(ChannelsView, { global: { plugins: [createPinia()] } });
    await w.findAll('.chip').find((c) => c.text().startsWith('All'))!.trigger('click');
    const original = names(w);
    expect(original).toEqual(['zeta', 'alpha', 'beta']);
    // Switching beta on lists it first on the server; its row must not jump.
    report.value.channels = [channel('beta', 'ok'), channel('zeta', 'ok'), channel('alpha', 'attention')];
    await flushPromises();
    expect(names(w)).toEqual(original);
    report.value.channels = [...report.value.channels, channel('new', 'empty', 0)];
    await flushPromises();
    expect(names(w)).toEqual([...original, 'new']);
  });

  it('starts from the channels in use and filters by what needs doing, with a count on each filter', async () => {
    const w = mount(ChannelsView, { global: { plugins: [createPinia()] } });
    const chip = (label: string) => w.findAll('.chip').find((c) => c.text().startsWith(label))!;
    expect(names(w)).toEqual(['zeta', 'alpha']);
    expect(chip('Needs attention').text()).toBe('Needs attention 1');
    await chip('Needs attention').trigger('click');
    expect(names(w)).toEqual(['alpha']);
    await chip('Off').trigger('click');
    expect(names(w)).toEqual(['beta']);
  });

  it('shows every station when nothing is in use yet, and a chosen channel the filter would hide', async () => {
    report.value.channels = [channel('a', 'empty', 0), channel('b', 'disabled')];
    const w = mount(ChannelsView, { props: { id: 'b' }, global: { plugins: [createPinia()] } });
    expect(names(w)).toEqual(['a', 'b']);
  });

  it('opens the chosen channel, and leaves for the list when it goes away', async () => {
    const w = mount(ChannelsView, { props: { id: 'alpha' }, global: { plugins: [createPinia()] } });
    expect(w.find('.detail').text()).toBe('alpha');
    expect(w.find('.row.selected .name').text()).toBe('alpha');
    report.value.channels = [channel('zeta', 'ok')];
    await flushPromises();
    expect(replace).toHaveBeenCalledWith('/channels');
  });
});
