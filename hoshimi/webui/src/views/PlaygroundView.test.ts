import { flushPromises, mount } from '@vue/test-utils';
import { createPinia } from 'pinia';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { createMemoryHistory, createRouter } from 'vue-router';
import PlaygroundView from '@/views/PlaygroundView.vue';

// Stubbed before the API client is made: openapi-fetch keeps the fetch it was created with.
const state = vi.hoisted(() => {
  const route = vi.fn();
  const fetch = (...args: unknown[]) => route(...args);
  vi.stubGlobal('fetch', fetch);
  return { error: vi.fn(), show: vi.fn(), route, fetch };
});
vi.mock('@/api/queries/app', () => ({
  useModels: () => ({ data: { value: { data: [{ id: 'test/model', owned_by: 'test' }, { id: 'other/model', owned_by: 'test' }] } }, isPending: { value: false }, isFetching: { value: false } }),
  useModelSpecs: () => ({ data: { value: { data: [{ id: 'test/model', vision: false, efforts: [] }] } } }),
}));
vi.mock('@/api/conversations', async (importOriginal) => ({ ...(await importOriginal<typeof import('@/api/conversations')>()), useConversations: () => ({ data: { value: { conversations: [] } }, isPending: { value: false }, refetch: vi.fn() }), useRefreshConversations: () => vi.fn() }));
vi.mock('@/ui', async (importOriginal) => ({ ...(await importOriginal<typeof import('@/ui')>()), useSnackbar: () => ({ error: state.error, show: state.show }) }));

// -- a server in memory: conversations as trees, replies streamed by the test -----------------
interface Msg { id: string; parent_id: string | null; role: 'user' | 'assistant'; content: string; model: string; effort: string; status: string; error: string; notices: { kind: string; name: string; model: string }[]; created_at: number; attachments: unknown[] }
interface Conv { id: string; title: string; title_state: string; model: string; effort: string; archived: boolean; temporary: boolean; branched_from: string; created_at: number; updated_at: number; current_leaf: string; messages: Msg[] }
let server: { conversations: Map<string, Conv>; session: string | null; calls: { method: string; path: string; body: unknown }[]; ids: number; clock: number };
/** The reply streams the test has not yet finished: it writes their events and closes them. */
let streams: { send: (event: object) => void; close: () => void; signal: AbortSignal }[];

const json = (body: unknown, status = 200) => new Response(status === 204 ? null : JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
const summary = ({ messages: _m, current_leaf: _l, ...rest }: Conv) => rest;
function message(c: Conv, parent: string | null, role: Msg['role'], content: string, extra: Partial<Msg> = {}): Msg {
  const m: Msg = { id: `m${++server.ids}`, parent_id: parent, role, content, model: '', effort: '', status: 'complete', error: '', notices: [], created_at: ++server.clock, attachments: [], ...extra };
  c.messages.push(m);
  c.current_leaf = m.id;
  if (!c.title && role === 'user') Object.assign(c, { title: content, title_state: 'provisional' });
  return m;
}
function sse(signal: AbortSignal | null | undefined, first: object[]) {
  let controller!: ReadableStreamDefaultController<Uint8Array>;
  const body = new ReadableStream<Uint8Array>({ start: (c) => { controller = c; } });
  const stream = {
    send: (event: object) => controller.enqueue(new TextEncoder().encode(`data: ${JSON.stringify(event)}\n\n`)),
    close: () => controller.close(),
    signal: signal ?? new AbortController().signal,
  };
  stream.signal.addEventListener('abort', () => controller.error(new DOMException('Aborted', 'AbortError')), { once: true });
  first.forEach(stream.send);
  streams.push(stream);
  return new Response(body, { headers: { 'Content-Type': 'text/event-stream' } });
}

async function handle(input: RequestInfo | URL, init?: RequestInit): Promise<Response> {
  const request = input instanceof Request ? input : new Request(new URL(String(input), 'http://localhost'), init);
  const url = new URL(request.url, 'http://localhost');
  const path = url.pathname.replace(/^.*\/api\/v1\/playground/, '');
  const text = request.method === 'GET' ? '' : await request.text();
  const body = text && request.headers.get('content-type')?.includes('json') ? JSON.parse(text) : text;
  server.calls.push({ method: request.method, path: path + url.search, body });
  const [, kind, id, sub, mid, action] = path.split('/');
  const c = id ? server.conversations.get(id) : undefined;
  if (kind === 'session') {
    if (request.method === 'PUT') server.session = (body as { conversation_id: string | null }).conversation_id;
    return json({ conversation_id: server.session });
  }
  if (kind === 'attachments' && request.method === 'POST') {
    return json({ id: `a${++server.ids}`, kind: 'file', name: url.searchParams.get('name'), mime: url.searchParams.get('type') || 'text/plain', size: text.length, width: null, height: null }, 201);
  }
  if (kind === 'conversations' && !id && request.method === 'POST') {
    const b = body as { model: string; effort: string; temporary: boolean };
    const conv: Conv = { id: `c${++server.ids}`, title: '', title_state: '', model: b.model, effort: b.effort, archived: false, temporary: b.temporary, branched_from: '', created_at: 1, updated_at: 1, current_leaf: '', messages: [] };
    server.conversations.set(conv.id, conv);
    return json(conv, 201);
  }
  if (!c) return json({ code: 'not_found', message: 'No such conversation' }, 404);
  if (!sub && request.method === 'GET') return json(c);
  if (!sub && request.method === 'PATCH') {
    Object.assign(c, Object.fromEntries(Object.entries(body as object).filter(([, v]) => v !== undefined)));
    return json(summary(c));
  }
  if (!sub && request.method === 'DELETE') {
    server.conversations.delete(c.id);
    return json(null, 204);
  }
  if (sub === 'title') {
    if (c.title_state === 'provisional') Object.assign(c, { title: 'A generated title', title_state: 'auto' });
    return json(summary(c));
  }
  if (sub === 'branch') {
    const conv: Conv = { ...structuredClone(c), id: `c${++server.ids}`, branched_from: c.id };
    server.conversations.set(conv.id, conv);
    return json(conv, 201);
  }
  if (sub === 'messages' && !mid) {
    const b = body as { parent_id: string | null; content: string; attachment_ids: string[]; model: string };
    const question = message(c, b.parent_id, 'user', b.content, { attachments: b.attachment_ids.map((a) => ({ id: a, kind: 'file', name: 'notes.md', mime: 'text/markdown', size: 4 })) });
    const reply = message(c, question.id, 'assistant', '', { status: 'streaming', model: b.model });
    return sse(request.signal, [{ type: 'user', message: question }, { type: 'assistant', message: reply }]);
  }
  if (sub === 'messages' && action === 'retry') {
    const target = c.messages.find((m) => m.id === mid)!;
    const reply = message(c, target.role === 'user' ? target.id : target.parent_id, 'assistant', '', { status: 'streaming', model: (body as { model?: string }).model || 'test/model' });
    return sse(request.signal, [{ type: 'assistant', message: reply }]);
  }
  if (sub === 'messages' && action === 'request') return json({ model: 'test/model', messages: [{ role: 'user', content: 'hello' }], stream: true });
  return json({ code: 'not_found', message: path }, 404);
}

/** Finishes the reply in the latest stream with ``content`` (and ``extra`` on the kept message). */
async function answer(content: string, extra: Partial<Msg> = {}) {
  const stream = streams[streams.length - 1];
  const c = [...server.conversations.values()].find((x) => x.messages.some((m) => m.status === 'streaming'))!;
  const reply = c.messages.find((m) => m.status === 'streaming')!;
  for (const piece of content.match(/.{1,6}/gs) ?? []) stream.send({ type: 'delta', text: piece });
  await flushPromises();
  Object.assign(reply, { content, status: 'complete', ...extra });
  stream.send({ type: 'done', message: reply });
  stream.close();
  await flushPromises();
}

async function openPlayground(path = '/playground') {
  const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/playground', component: PlaygroundView }] });
  await router.push(path);
  const wrapper = mount(PlaygroundView, { attachTo: document.body, global: { plugins: [router, createPinia()], stubs: { CatalogStatus: true } } });
  await flushPromises();
  return { wrapper, router };
}
type Wrapper = Awaited<ReturnType<typeof openPlayground>>['wrapper'];
async function ask(wrapper: Wrapper, text: string) {
  await wrapper.find('.composer textarea').setValue(text);
  await wrapper.find('.composer textarea').trigger('keydown', { key: 'Enter' });
  await flushPromises();
}
const turns = (wrapper: Wrapper) =>
  wrapper.findAll('.user-message, .assistant-message').map((m) => `${m.classes('user-message') ? 'Q' : 'A'}: ${(m.find('.bubble, .content').exists() ? m.find('.bubble, .content').text() : '').trim()}`);
const calls = (method: string, pattern: RegExp) => server.calls.filter((c) => c.method === method && pattern.test(c.path));

describe('Playground chat', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    server = { conversations: new Map(), session: null, calls: [], ids: 0, clock: 0 };
    streams = [];
    state.route.mockImplementation(handle);
    vi.stubGlobal('fetch', state.fetch);
    vi.stubGlobal('navigator', { ...navigator, clipboard: { writeText: vi.fn().mockResolvedValue(undefined) } });
    localStorage.clear();
  });
  afterEach(() => {
    vi.unstubAllGlobals();
    document.body.innerHTML = '';
  });

  it('starts a chat on the first send, streams the reply with no names beside it, and has it titled', async () => {
    const { wrapper } = await openPlayground();
    expect(wrapper.text()).toContain('What can I help with?');
    await ask(wrapper, 'What is **this**?');
    expect(calls('POST', /^\/conversations$/)[0].body).toEqual({ model: 'test/model', effort: '', temporary: false, title: '' });
    expect(server.session).toBe('c1');
    expect(wrapper.find('.assistant-message').text()).toContain('Answering…');
    await answer('It is a **gateway**.');
    expect(turns(wrapper)).toEqual(['Q: What is **this**?', 'A: It is a gateway.']);
    expect(wrapper.find('.assistant-message strong').text()).toBe('gateway');
    // No speaker labels: neither "You" nor "Assistant" is written beside a message.
    expect(wrapper.find('.thread').text()).not.toMatch(/\bYou\b|\bAssistant\b/);
    expect(calls('POST', /\/title$/)).toHaveLength(1);
    expect(server.conversations.get('c1')!.title).toBe('A generated title');
    wrapper.unmount();
  });

  it('edits a question into a second version, and moves between the versions', async () => {
    const { wrapper } = await openPlayground();
    await ask(wrapper, 'first try');
    await answer('first answer');
    await wrapper.find('.user-message button[aria-label="Edit message"]').trigger('click');
    await flushPromises();
    const editor = wrapper.find('.user-message .editor textarea');
    expect((editor.element as HTMLTextAreaElement).value).toBe('first try');
    await editor.setValue('second try');
    await wrapper.findAll('.user-message .editor md-filled-button, .user-message .editor button').find((b) => b.text() === 'Send')!.trigger('click');
    await flushPromises();
    expect(calls('POST', /\/messages$/)[1].body).toMatchObject({ parent_id: null, content: 'second try' });
    await answer('second answer');
    expect(turns(wrapper)).toEqual(['Q: second try', 'A: second answer']);
    expect(wrapper.find('.user-message .versions').text()).toContain('2 / 2');
    await wrapper.find('.user-message button[aria-label="Previous version"]').trigger('click');
    await flushPromises();
    expect(turns(wrapper)).toEqual(['Q: first try', 'A: first answer']);
    expect(calls('PATCH', /^\/conversations\/c1$/).at(-1)!.body).toEqual({ current_leaf: 'm3' });
    wrapper.unmount();
  });

  it('retries a reply beside the first', async () => {
    const { wrapper } = await openPlayground();
    await ask(wrapper, 'hello');
    await answer('first reply');
    await wrapper.find('.assistant-message button[aria-label="Try again"]').trigger('click');
    await flushPromises();
    expect(calls('POST', /\/retry$/)[0]).toMatchObject({ path: '/conversations/c1/messages/m3/retry', body: {} });
    await answer('second reply');
    expect(turns(wrapper)).toEqual(['Q: hello', 'A: second reply']);
    expect(wrapper.find('.assistant-message .versions').text()).toContain('2 / 2');
    wrapper.unmount();
  });

  it('copies a question, a reply, and a reply as an API request', async () => {
    const { wrapper } = await openPlayground();
    await ask(wrapper, 'hello');
    await answer('**hi**');
    const write = vi.mocked(navigator.clipboard.writeText);
    await wrapper.find('.user-message button[aria-label="Copy"]').trigger('click');
    await wrapper.find('.assistant-message button[aria-label="Copy"]').trigger('click');
    await flushPromises();
    expect(write.mock.calls.map((c) => c[0])).toEqual(['hello', '**hi**']);
    await wrapper.find('.assistant-message button[aria-label="More actions"]').trigger('click');
    await flushPromises();
    [...document.body.querySelectorAll<HTMLElement>('[role="menuitem"]')].find((e) => e.textContent?.includes('Copy as API request'))!.click();
    await flushPromises();
    const command = write.mock.calls.at(-1)![0];
    expect(command).toMatch(/^curl http:\/\/[^ ]+\/v1\/chat\/completions \\/);
    expect(command).toContain('Authorization: Bearer $HOSHIMI_API_KEY');
    expect(command).toContain('"model": "test/model"');
    expect(state.show).toHaveBeenLastCalledWith('API request copied');
    wrapper.unmount();
  });

  it('branches a reply into a new chat', async () => {
    const { wrapper } = await openPlayground();
    await ask(wrapper, 'hello');
    await answer('hi');
    await wrapper.find('.assistant-message button[aria-label="Branch in new chat"]').trigger('click');
    await flushPromises();
    expect(calls('POST', /\/branch$/)[0].body).toEqual({ message_id: 'm3' });
    expect(server.session).toBe('c4');
    expect(turns(wrapper)).toEqual(['Q: hello', 'A: hi']);
    expect(state.show).toHaveBeenLastCalledWith('Opened in a new chat');
    wrapper.unmount();
  });

  it('keeps a temporary chat off the record and deletes it when it is left', async () => {
    const { wrapper } = await openPlayground();
    await wrapper.find('button.temporary').trigger('click');
    expect(wrapper.find('button.temporary').attributes('aria-pressed')).toBe('true');
    expect(wrapper.find('.temporary-note').exists()).toBe(true);
    await ask(wrapper, 'secret');
    expect(calls('POST', /^\/conversations$/)[0].body).toMatchObject({ temporary: true });
    await answer('kept quiet');
    expect(calls('POST', /\/title$/)).toHaveLength(0);
    expect(server.session).toBeNull();
    expect(wrapper.find('.assistant-message button[aria-label="Branch in new chat"]').exists()).toBe(false);
    await wrapper.find('button.temporary').trigger('click');
    await flushPromises();
    expect(calls('DELETE', /^\/conversations\/c1$/)).toHaveLength(1);
    expect(turns(wrapper)).toEqual([]);
    wrapper.unmount();
  });

  it('stops a reply and keeps what came', async () => {
    const { wrapper } = await openPlayground();
    await ask(wrapper, 'hello');
    streams[0].send({ type: 'delta', text: 'partial' });
    await flushPromises();
    await wrapper.find('button[aria-label="Stop"]').trigger('click');
    await flushPromises();
    expect(streams[0].signal.aborted).toBe(true);
    expect(wrapper.find('.assistant-message').text()).toContain('partial');
    expect(wrapper.find('.assistant-message .ended').text()).toBe('Stopped');
    expect(wrapper.find('button[aria-label="Send"]').exists()).toBe(true);
    wrapper.unmount();
  });

  it('shows how a reply was made, and a failed one with a way to retry it', async () => {
    const { wrapper } = await openPlayground();
    await ask(wrapper, 'look');
    await answer('a square', { notices: [{ kind: 'image-described', name: 'dot.png', model: 'seer' }] });
    expect(wrapper.find('.notices').text()).toContain('The image “dot.png” was described by seer');
    await ask(wrapper, 'again');
    await answer('', { status: 'error', error: 'the station is down' });
    expect(wrapper.find('.failed').text()).toContain('This reply failed. the station is down');
    await wrapper.findAll('.failed md-text-button, .failed button').at(-1)!.trigger('click');
    await flushPromises();
    expect(calls('POST', /\/retry$/)).toHaveLength(1);
    wrapper.unmount();
  });

  it('comes back to the chat open last', async () => {
    server.conversations.set('old', {
      id: 'old', title: 'Earlier', title_state: 'auto', model: 'other/model', effort: '', archived: false, temporary: false, branched_from: '', created_at: 0, updated_at: 0, current_leaf: 'o2',
      messages: [
        { id: 'o1', parent_id: null, role: 'user', content: 'before', model: '', effort: '', status: 'complete', error: '', notices: [], created_at: 1, attachments: [] },
        { id: 'o2', parent_id: 'o1', role: 'assistant', content: 'a **kept** reply', model: 'other/model', effort: '', status: 'complete', error: '', notices: [], created_at: 2, attachments: [] },
      ],
    });
    server.session = 'old';
    const { wrapper } = await openPlayground();
    expect(turns(wrapper)).toEqual(['Q: before', 'A: a kept reply']);
    expect(wrapper.find('.chat-header').text()).toContain('other/model');
    await ask(wrapper, 'more');
    expect(calls('POST', /\/messages$/)[0]).toMatchObject({ path: '/conversations/old/messages', body: { parent_id: 'o2', content: 'more', model: 'other/model' } });
    wrapper.unmount();
  });

  it('starts afresh when opened to try one model, and lets the address go', async () => {
    server.session = 'old';
    const { wrapper, router } = await openPlayground('/playground?model=other/model');
    expect(calls('GET', /^\/session/)).toHaveLength(0);
    expect(wrapper.find('.chat-header').text()).toContain('other/model');
    expect(router.currentRoute.value.query).toEqual({});
    wrapper.unmount();
  });

  it('sends a file with the message, and refuses one it cannot read', async () => {
    const { wrapper } = await openPlayground();
    const input = wrapper.find('input[type="file"]');
    Object.defineProperty(input.element, 'files', { value: [new File(['# notes'], 'notes.md', { type: 'text/markdown' })], configurable: true });
    await input.trigger('change');
    await flushPromises();
    expect(calls('POST', /^\/attachments\?name=notes\.md/)).toHaveLength(1);
    expect(wrapper.find('.tray .attachment .name').text()).toBe('notes.md');
    await ask(wrapper, 'Summarise');
    expect(calls('POST', /\/messages$/)[0].body).toMatchObject({ content: 'Summarise', attachment_ids: ['a1'] });
    expect(wrapper.find('.tray').exists()).toBe(false);
    expect(wrapper.find('.user-message .attachment .name').text()).toBe('notes.md');
    Object.defineProperty(input.element, 'files', { value: [new File([new Uint8Array([0, 1])], 'blob.bin', { type: 'application/octet-stream' })], configurable: true });
    await input.trigger('change');
    await flushPromises();
    expect(state.error).toHaveBeenCalledWith('Can’t read “blob.bin”: send an image, a text file or a PDF.');
    wrapper.unmount();
  });

  it('starts a new chat from the sidebar, and recalls the last question for editing', async () => {
    const { wrapper } = await openPlayground();
    await ask(wrapper, 'hello');
    await answer('hi');
    await wrapper.find('.composer textarea').trigger('keydown', { key: 'ArrowUp' });
    await flushPromises();
    expect(wrapper.find('.user-message .editor').exists()).toBe(true);
    await wrapper.find('.chat-sidebar .new').trigger('click');
    await flushPromises();
    expect(turns(wrapper)).toEqual([]);
    expect(server.session).toBeNull();
    wrapper.unmount();
  });
});
