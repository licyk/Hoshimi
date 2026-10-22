import { flushPromises, mount } from '@vue/test-utils';
import { createPinia } from 'pinia';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { createMemoryHistory, createRouter } from 'vue-router';
import PlaygroundView from '@/views/PlaygroundView.vue';

const state = vi.hoisted(() => ({ error: vi.fn(), show: vi.fn() }));
// The kept conversations, in memory: what the page files and what it opens.
const kept = vi.hoisted(() => {
  const api = {
    create: vi.fn(async (model: string, effort: string) => ({ id: 'c1', title: '', model, effort, archived: false, created_at: 0, updated_at: 0, messages: [] })),
    get: vi.fn(async (_id: string): Promise<unknown> => { throw new Error('missing'); }),
    update: vi.fn(async () => ({})),
    remove: vi.fn(async () => undefined),
    append: vi.fn(async () => ({})),
    session: vi.fn(async (): Promise<{ conversation_id: string | null }> => ({ conversation_id: null })),
    setSession: vi.fn(async (id: string | null) => ({ conversation_id: id })),
  };
  return { api, refresh: vi.fn() };
});
vi.mock('@/api/conversations', () => ({ conversationsApi: kept.api, useRefreshConversations: () => kept.refresh }));
vi.mock('@/api/queries/app', () => ({
  useModels: () => ({ data: { value: { data: [{ id: 'test/model' }] } }, isPending: { value: false }, isFetching: { value: false } }),
  useModelSpecs: () => ({ data: { value: { data: [] } } }),
}));
vi.mock('@/ui', async (importOriginal) => ({ ...(await importOriginal<typeof import('@/ui')>()), useSnackbar: () => ({ error: state.error, show: state.show }) }));

async function openPlayground() {
  const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/playground', component: PlaygroundView }] });
  await router.push('/playground');
  return mount(PlaygroundView, { global: { plugins: [router, createPinia()], stubs: { CatalogStatus: true, ConversationList: true } } });
}

describe('Playground live output', () => {
  let source: ReadableStreamDefaultController<Uint8Array>;
  let signal: AbortSignal;
  const emit = (text: string) => source.enqueue(new TextEncoder().encode(`data: ${JSON.stringify({ choices: [{ index: 0, delta: { content: text } }] })}\n\n`));
  beforeEach(() => {
    vi.clearAllMocks();
    vi.stubGlobal('fetch', vi.fn().mockImplementation((_url, options) => {
      signal = options.signal;
      const stream = new ReadableStream<Uint8Array>({ start: (controller) => { source = controller; } });
      signal.addEventListener('abort', () => source.error(new DOMException('Aborted', 'AbortError')), { once: true });
      return Promise.resolve(new Response(stream));
    }));
  });
  afterEach(() => vi.unstubAllGlobals());

  it('renders every delta while generation is still pending, then replays the full reply', async () => {
    const wrapper = await openPlayground();
    await wrapper.find('textarea').setValue('hello');
    await wrapper.find('textarea').trigger('keydown', { key: 'Enter' });
    await flushPromises();
    emit('第一段');
    await flushPromises();
    expect(wrapper.find('.assistant .bubble').text()).toBe('第一段');
    expect(wrapper.find('button[aria-label="Stop"]').exists()).toBe(true);
    emit('，第二段');
    await flushPromises();
    expect(wrapper.find('.assistant .bubble').text()).toBe('第一段，第二段');
    source.enqueue(new TextEncoder().encode('data: [DONE]\n\n'));
    await flushPromises();
    expect(wrapper.find('button[aria-label="Send"]').exists()).toBe(true);
    await wrapper.find('textarea').setValue('continue');
    await wrapper.find('textarea').trigger('keydown', { key: 'Enter' });
    await flushPromises();
    const fetch = vi.mocked(globalThis.fetch);
    expect(JSON.parse(fetch.mock.calls[1][1]!.body as string).messages).toEqual([
      { role: 'user', content: 'hello' }, { role: 'assistant', content: '第一段，第二段' }, { role: 'user', content: 'continue' },
    ]);
    wrapper.unmount();
    await flushPromises();
  });

  it('copies a code block from a reply and says so', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    vi.stubGlobal('navigator', { ...navigator, clipboard: { writeText } });
    const wrapper = await openPlayground();
    await wrapper.find('textarea').setValue('show me');
    await wrapper.find('textarea').trigger('keydown', { key: 'Enter' });
    await flushPromises();
    emit('Run:\n\n```bash\nhoshimi serve\n```');
    source.enqueue(new TextEncoder().encode('data: [DONE]\n\n'));
    await flushPromises();
    expect(wrapper.find('.assistant .code-lang').text()).toBe('bash');
    await wrapper.find('.assistant button.code-copy').trigger('click');
    await flushPromises();
    expect(writeText).toHaveBeenCalledWith('hoshimi serve');
    expect(state.show).toHaveBeenCalledWith('Copied to the clipboard');
    wrapper.unmount();
    await flushPromises();
  });

  it('renders a reply as Markdown while it streams, and keeps what the user typed as typed', async () => {
    const wrapper = await openPlayground();
    await wrapper.find('textarea').setValue('**not bold** <b>me</b>');
    await wrapper.find('textarea').trigger('keydown', { key: 'Enter' });
    await flushPromises();
    emit('Here is **bold**');
    await flushPromises();
    expect(wrapper.find('.assistant .bubble strong').text()).toBe('bold');
    emit(' and a list:\n\n- one\n- two');
    await flushPromises();
    expect(wrapper.findAll('.assistant .bubble li').map((li) => li.text())).toEqual(['one', 'two']);
    expect(wrapper.find('.user .bubble').text()).toBe('**not bold** <b>me</b>');
    expect(wrapper.find('.user .bubble strong').exists()).toBe(false);
    source.enqueue(new TextEncoder().encode('data: [DONE]\n\n'));
    await flushPromises();
    wrapper.unmount();
    await flushPromises();
  });

  it('stops generation while keeping the received text', async () => {
    const wrapper = await openPlayground();
    await wrapper.find('textarea').setValue('hello');
    await wrapper.find('textarea').trigger('keydown', { key: 'Enter' });
    await flushPromises();
    emit('partial');
    await flushPromises();
    await wrapper.find('button[aria-label="Stop"]').trigger('click');
    await flushPromises();
    expect(signal.aborted).toBe(true);
    expect(wrapper.find('.assistant .bubble').text()).toBe('partial');
    expect(state.error).not.toHaveBeenCalled();
    expect(wrapper.find('button[aria-label="Send"]').exists()).toBe(true);
    wrapper.unmount();
  });

  it('preserves partial output and shows failures sent inside the stream', async () => {
    const wrapper = await openPlayground();
    await wrapper.find('textarea').setValue('hello');
    await wrapper.find('textarea').trigger('keydown', { key: 'Enter' });
    await flushPromises();
    emit('partial');
    source.enqueue(new TextEncoder().encode('data: {"error":{"message":"Provider disconnected","code":"upstream_error"}}\n\n'));
    await flushPromises();
    expect(wrapper.find('.assistant .bubble').text()).toBe('partial');
    expect(state.error).toHaveBeenCalledWith('Provider disconnected');
    expect(wrapper.find('button[aria-label="Send"]').exists()).toBe(true);
    wrapper.unmount();
  });

  it('starts a new chat: the reply in progress stops, is kept with its conversation, and the session is let go', async () => {
    const wrapper = await openPlayground();
    await wrapper.find('textarea').setValue('hello');
    await wrapper.find('textarea').trigger('keydown', { key: 'Enter' });
    await flushPromises();
    emit('partial');
    await flushPromises();
    await wrapper.find('button[aria-label="New chat"]').trigger('click');
    await flushPromises();
    expect(signal.aborted).toBe(true);
    expect(wrapper.findAll('.turn')).toHaveLength(0);
    expect(kept.api.append.mock.calls).toEqual([['c1', 'user', 'hello'], ['c1', 'assistant', 'partial', 'test/model']]);
    expect(kept.api.setSession).toHaveBeenLastCalledWith(null);
    expect(state.error).not.toHaveBeenCalled();
    wrapper.unmount();
  });

  it('keeps a conversation as it happens: created on the first send, each message filed in order', async () => {
    const wrapper = await openPlayground();
    for (const [question, answer] of [['first', 'one'], ['second', 'two']]) {
      await wrapper.find('textarea').setValue(question);
      await wrapper.find('textarea').trigger('keydown', { key: 'Enter' });
      await flushPromises();
      emit(answer);
      source.enqueue(new TextEncoder().encode('data: [DONE]\n\n'));
      await flushPromises();
    }
    expect(kept.api.create).toHaveBeenCalledOnce();
    expect(kept.api.create).toHaveBeenCalledWith('test/model', '');
    expect(kept.api.setSession).toHaveBeenCalledWith('c1');
    expect(kept.api.append.mock.calls).toEqual([
      ['c1', 'user', 'first'], ['c1', 'assistant', 'one', 'test/model'], ['c1', 'user', 'second'], ['c1', 'assistant', 'two', 'test/model'],
    ]);
    expect(kept.refresh).toHaveBeenCalled();
    wrapper.unmount();
    await flushPromises();
  });

  it('comes back to the conversation open last, and answers in it', async () => {
    kept.api.session.mockResolvedValueOnce({ conversation_id: 'old' });
    kept.api.get.mockResolvedValueOnce({
      id: 'old', title: 'Earlier', model: 'test/model', effort: '', archived: false, created_at: 0, updated_at: 0,
      messages: [{ role: 'user', content: 'before', model: '', created_at: 0 }, { role: 'assistant', content: 'a **kept** reply', model: 'test/model', created_at: 0 }],
    });
    const wrapper = await openPlayground();
    await flushPromises();
    expect(kept.api.get).toHaveBeenCalledWith('old');
    expect(wrapper.findAll('.turn .bubble').map((b) => b.text())).toEqual(['before', 'a kept reply']);
    expect(wrapper.find('.assistant strong').text()).toBe('kept');
    await wrapper.find('textarea').setValue('more');
    await wrapper.find('textarea').trigger('keydown', { key: 'Enter' });
    await flushPromises();
    const fetch = vi.mocked(globalThis.fetch);
    expect(JSON.parse(fetch.mock.calls[0][1]!.body as string).messages.map((m: { content: string }) => m.content)).toEqual(['before', 'a **kept** reply', 'more']);
    expect(kept.api.create).not.toHaveBeenCalled();
    expect(kept.api.append).toHaveBeenCalledWith('old', 'user', 'more');
    source.enqueue(new TextEncoder().encode('data: [DONE]\n\n'));
    wrapper.unmount();
    await flushPromises();
  });

  it('starts afresh when opened to try one model', async () => {
    kept.api.session.mockResolvedValueOnce({ conversation_id: 'old' });
    const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/playground', component: PlaygroundView }] });
    await router.push('/playground?model=test/model');
    const wrapper = mount(PlaygroundView, { global: { plugins: [router, createPinia()], stubs: { CatalogStatus: true, ConversationList: true } } });
    await flushPromises();
    expect(kept.api.session).not.toHaveBeenCalled();
    expect(wrapper.findAll('.turn')).toHaveLength(0);
    expect(wrapper.find('button[aria-label^="Model"]').text()).toContain('test/model');
    // Let go of: a reload goes back to the session, not to a fresh start.
    expect(router.currentRoute.value.query).toEqual({});
    wrapper.unmount();
  });

  it('keeps every control inside the composer, with no row of controls above the thread', async () => {
    const wrapper = await openPlayground();
    const composer = wrapper.find('.composer');
    expect(composer.find('textarea').exists()).toBe(true);
    expect(composer.find('button[aria-label^="Model"]').exists()).toBe(true);
    expect(composer.find('button[aria-label="Send"]').attributes('disabled')).toBeDefined();
    // Nothing to leave yet: New chat arrives with the first message.
    expect(wrapper.find('button[aria-label="New chat"]').exists()).toBe(false);
    await wrapper.find('textarea').setValue('hello');
    expect(composer.find('button[aria-label="Send"]').attributes('disabled')).toBeUndefined();
    await wrapper.find('textarea').trigger('keydown', { key: 'Enter', shiftKey: true });
    await flushPromises();
    expect(globalThis.fetch).not.toHaveBeenCalled();
    await composer.find('button[aria-label="Send"]').trigger('click');
    await flushPromises();
    expect(globalThis.fetch).toHaveBeenCalledOnce();
    expect(wrapper.find('.composer button[aria-label="New chat"]').exists()).toBe(true);
    wrapper.unmount();
  });
});
