import { flushPromises, mount } from '@vue/test-utils';
import { createPinia } from 'pinia';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ref } from 'vue';
import ConversationList from '@/components/ConversationList.vue';

const now = Date.now() / 1000;
const recent = [
  { id: 'a', title: 'Setting up the gateway', model: 'm1', effort: '', archived: false, created_at: now - 60, updated_at: now - 60 },
  { id: 'b', title: '', model: '', effort: '', archived: false, created_at: now - 7200, updated_at: now - 7200 },
];
const archived = [{ id: 'z', title: 'Put away', model: 'm2', effort: '', archived: true, created_at: now - 90000, updated_at: now - 90000 }];
const kept = vi.hoisted(() => ({ update: vi.fn(async () => ({})), remove: vi.fn(async () => undefined), refresh: vi.fn(), show: vi.fn(), error: vi.fn() }));

vi.mock('@/api/conversations', () => ({
  conversationsApi: { update: kept.update, remove: kept.remove },
  useConversations: (isArchived: { value: boolean }) => ({
    isPending: ref(false),
    data: { get value() { return { conversations: isArchived.value ? archived : recent }; } },
  }),
  useRefreshConversations: () => kept.refresh,
}));
vi.mock('@/ui', async (importOriginal) => ({ ...(await importOriginal<typeof import('@/ui')>()), useSnackbar: () => ({ show: kept.show, error: kept.error }) }));

const open = (current: string | null = 'a') => mount(ConversationList, { props: { current }, attachTo: document.body, global: { plugins: [createPinia()] } });
/** Opens a conversation's menu and picks an item by its label. */
async function pick(w: ReturnType<typeof open>, index: number, label: string) {
  await w.findAll('.item md-icon-button[aria-label="More"]')[index].trigger('click');
  await flushPromises();
  const item = [...document.body.querySelectorAll<HTMLElement>('[role="menuitem"]')].find((e) => e.textContent?.trim() === label);
  expect(item, `no menu item ${label}`).toBeTruthy();
  item!.click();
  await flushPromises();
}

describe('ConversationList', () => {
  beforeEach(() => vi.clearAllMocks());
  afterEach(() => {
    document.body.innerHTML = '';
  });

  it('lists the recent conversations, names an untitled one, and marks the open one', () => {
    const w = open();
    const items = w.findAll('.item');
    expect(items.map((i) => i.find('.title').text())).toEqual(['Setting up the gateway', 'New conversation']);
    expect(items[0].find('.meta').text()).toBe('1 minute ago · m1');
    expect(items[0].classes()).toContain('current');
    expect(items[0].find('.open').attributes('aria-current')).toBe('true');
    expect(items[1].find('.open').attributes('aria-current')).toBeUndefined();
    w.unmount();
  });

  it('opens one, or starts a new one', async () => {
    const w = open();
    await w.findAll('.item .open')[1].trigger('click');
    await w.find('.new').trigger('click');
    expect(w.emitted('select')).toEqual([['b']]);
    expect(w.emitted('new')).toHaveLength(1);
    w.unmount();
  });

  it('archives from the recent list and restores from the archive', async () => {
    const w = open();
    await pick(w, 0, 'Archive');
    expect(kept.update).toHaveBeenCalledWith('a', { archived: true });
    expect(kept.show).toHaveBeenCalledWith('Conversation archived');
    expect(kept.refresh).toHaveBeenCalled();
    await w.findAll('[role="radio"]').find((r) => r.text() === 'Archived')!.trigger('click');
    expect(w.findAll('.item .title').map((t) => t.text())).toEqual(['Put away']);
    await pick(w, 0, 'Restore');
    expect(kept.update).toHaveBeenLastCalledWith('z', { archived: false });
    w.unmount();
  });

  it('deletes only once confirmed, and says which one went', async () => {
    const w = open();
    await pick(w, 0, 'Delete');
    expect(kept.remove).not.toHaveBeenCalled();
    expect(document.body.textContent).toContain('“Setting up the gateway” and all its messages are removed for good.');
    const confirm = [...document.body.querySelectorAll<HTMLElement>('md-filled-button, button')].find((b) => b.textContent?.trim() === 'Delete' && b.closest('[role="dialog"], .dialog'));
    expect(confirm).toBeTruthy();
    confirm!.click();
    await flushPromises();
    expect(kept.remove).toHaveBeenCalledWith('a');
    expect(w.emitted('deleted')).toEqual([['a']]);
    expect(kept.show).toHaveBeenCalledWith('Conversation deleted');
    w.unmount();
  });
});
