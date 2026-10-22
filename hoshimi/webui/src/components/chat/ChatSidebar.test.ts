import { flushPromises, mount } from '@vue/test-utils';
import { createPinia } from 'pinia';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { ref } from 'vue';
import ChatSidebar from '@/components/chat/ChatSidebar.vue';

const DAY = 86_400;
// Times from the last local midnight, so every chat lands in its group whenever the test runs.
const midnight = new Date().setHours(0, 0, 0, 0) / 1000;
const chat = (id: string, title: string, at: number, extra: object = {}) => ({
  id, title, title_state: title ? 'auto' : '', model: 'm', effort: '', archived: false, temporary: false, branched_from: '', created_at: at, updated_at: at, ...extra,
});
const recent = [
  chat('a', 'Setting up the gateway', Date.now() / 1000),
  chat('b', '', midnight - DAY / 2),
  chat('c', 'Branched idea', midnight - 3 * DAY, { branched_from: 'a' }),
  chat('d', 'Old notes', midnight - 90 * DAY),
];
const archived = [chat('z', 'Put away', midnight - 20 * DAY, { archived: true })];

vi.mock('@/api/conversations', () => ({
  useConversations: (isArchived: { value: boolean }) => ({
    isPending: ref(false),
    refetch: vi.fn(),
    data: { get value() { return { conversations: isArchived.value ? archived : recent }; } },
  }),
}));

const open = () => mount(ChatSidebar, { props: { current: 'a' }, attachTo: document.body, global: { plugins: [createPinia()] } });
const groups = (w: ReturnType<typeof open>) => w.findAll('.group').map((g) => [g.find('.label').text(), ...g.findAll('.title').map((t) => t.text())]);

describe('ChatSidebar', () => {
  afterEach(() => {
    document.body.innerHTML = '';
  });

  it('groups the chats by when they were last used, names an untitled one, and marks the open one', () => {
    const w = open();
    expect(groups(w)).toEqual([
      ['Today', 'Setting up the gateway'],
      ['Yesterday', 'New chat'],
      ['Previous 7 days', 'Branched idea'],
      ['Older', 'Old notes'],
    ]);
    expect(w.find('.item.current .title').text()).toBe('Setting up the gateway');
    expect(w.find('.item.current .open').attributes('aria-current')).toBe('page');
    expect(w.findAll('.item')[2].find('.branched').exists()).toBe(true);
    w.unmount();
  });

  it('searches the titles', async () => {
    const w = open();
    await w.find('.search input').setValue('NOTES');
    expect(groups(w)).toEqual([['Older', 'Old notes']]);
    await w.find('.search input').setValue('nothing like it');
    expect(w.find('.none').text()).toBe('No chats match “nothing like it”');
    w.unmount();
  });

  it('opens a chat, starts a new one, and hands its menu actions to the page', async () => {
    const w = open();
    await w.findAll('.item .open')[1].trigger('click');
    await w.find('.new').trigger('click');
    expect(w.emitted('select')).toEqual([['b']]);
    expect(w.emitted('new')).toHaveLength(1);
    await w.findAll('.item md-icon-button')[0].trigger('click');
    await flushPromises();
    [...document.body.querySelectorAll<HTMLElement>('[role="menuitem"]')].find((e) => e.textContent?.trim() === 'Archive')!.click();
    await flushPromises();
    expect(w.emitted('act')![0]).toEqual(['archive', recent[0]]);
    w.unmount();
  });

  it('shows the archive on its own, with a way back', async () => {
    const w = open();
    await w.find('.archive-link').trigger('click');
    expect(groups(w)).toEqual([['Previous 30 days', 'Put away']]);
    expect(w.find('.search').exists()).toBe(false);
    await w.findAll('.item md-icon-button')[0].trigger('click');
    await flushPromises();
    expect([...document.body.querySelectorAll('[role="menuitem"]')].map((e) => e.textContent?.trim())).toEqual(['Rename', 'Restore', 'Delete']);
    await w.find('.back').trigger('click');
    expect(w.findAll('.title').map((t) => t.text())).toContain('Setting up the gateway');
    w.unmount();
  });
});
