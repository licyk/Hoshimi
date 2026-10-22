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
  return { error: vi.fn(), show: vi.fn(), route, fetch, tools: [] as string[] };
});
vi.mock('@/api/queries/app', () => ({
  useModels: () => ({ data: { value: { data: [{ id: 'test/model', owned_by: 'test' }, { id: 'other/model', owned_by: 'test' }] } }, isPending: { value: false }, isFetching: { value: false } }),
  useModelSpecs: () => ({ data: { value: { data: [{ id: 'test/model', vision: false, efforts: [] }] } } }),
  useSettings: () => ({ data: { value: { playground: { tools: state.tools } } } }),
}));
vi.mock('@/api/conversations', async (importOriginal) => ({ ...(await importOriginal<typeof import('@/api/conversations')>()), useConversations: () => ({ data: { value: { conversations: [] } }, isPending: { value: false }, refetch: vi.fn() }), useRefreshConversations: () => vi.fn() }));
vi.mock('@/ui', async (importOriginal) => ({ ...(await importOriginal<typeof import('@/ui')>()), useSnackbar: () => ({ error: state.error, show: state.show }) }));

// -- a server in memory: conversations as trees, replies streamed by the test -----------------
interface Msg { id: string; parent_id: string | null; role: 'user' | 'assistant' | 'checkpoint'; content: string; model: string; effort: string; status: string; error: string; notices: { kind: string; name: string; model: string }[]; created_at: number; attachments: unknown[]; reasoning?: string; reasoning_ms?: number; steps?: unknown[]; duration_ms?: number; waited_ms?: number; finished_at?: number }
interface Conv { id: string; title: string; title_state: string; model: string; effort: string; archived: boolean; temporary: boolean; branched_from: string; created_at: number; updated_at: number; current_leaf: string; messages: Msg[]; tools_off?: string[]; tools_on?: string[]; approval?: string }
let server: { conversations: Map<string, Conv>; session: string | null; calls: { method: string; path: string; body: unknown }[]; ids: number; clock: number; terminals?: unknown[]; steerOpen?: boolean; context?: object };
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
    const b = body as { model: string; effort: string; temporary: boolean; tools_off?: string[]; tools_on?: string[]; approval?: string };
    const conv: Conv = {
      id: `c${++server.ids}`, title: '', title_state: '', model: b.model, effort: b.effort, archived: false, temporary: b.temporary, branched_from: '', created_at: 1, updated_at: 1,
      current_leaf: '', messages: [], tools_off: b.tools_off ?? [], tools_on: b.tools_on ?? [], approval: b.approval ?? '',
    };
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
  if (sub === 'approvals') return json(null, 204);
  if (sub === 'steer') return server.steerOpen ? json({ id: `steer${++server.ids}` }, 202) : json({ code: 'conflict', message: 'No reply is running' }, 409);
  if (sub === 'terminals' && request.method === 'GET') return json({ terminals: server.terminals ?? [] });
  if (sub === 'terminals' && request.method === 'DELETE') {
    server.terminals = [];
    return json(null, 204);
  }
  if (sub === 'context') return server.context ? json(server.context) : json({ code: 'not_found', message: 'no context' }, 404);
  if (sub === 'compact') {
    const record = { type: 'compaction', id: `cmp${++server.ids}`, status: 'running', trigger: 'manual', phase: 'manual', round: 0, summary: '', recent: [], tokens_before: 0, tokens_after: 0, model: '', ms: 0, error: '', at: 1 };
    const checkpoint = message(c, c.current_leaf || null, 'checkpoint', '', { status: 'streaming', steps: [record] });
    return sse(request.signal, [{ type: 'checkpoint', message: checkpoint }]);
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
/** The header's one button for what comes next: its name. */
const corner = (wrapper: Wrapper) => wrapper.find('.chat-header .corner').attributes('title');

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
    expect(calls('POST', /^\/conversations$/)[0].body).toEqual({ model: 'test/model', effort: '', temporary: false, title: '', tools_on: [], tools_off: [], approval: '' });
    expect(server.session).toBe('c1');
    expect(wrapper.find('.assistant-message').text()).toContain('Answering…');
    await answer('It is a **gateway**.');
    expect(turns(wrapper)).toEqual(['Q: What is **this**?', 'A: It is a gateway.']);
    expect(wrapper.find('.assistant-message strong').text()).toBe('gateway');
    // No speaker labels: neither "You" nor "Assistant" is written beside a message.
    expect(wrapper.find('.thread').text()).not.toMatch(/\bYou\b|\bAssistant\b/);
    expect(calls('POST', /\/title$/)).toHaveLength(1);
    expect(server.conversations.get('c1')!.title).toBe('A generated title');
    // A chat with messages offers a new one; the model is picked in the composer, not the header.
    expect(corner(wrapper)).toBe('New chat');
    expect(wrapper.find('.composer').text()).toContain('test/model');
    expect(wrapper.find('.chat-header').text()).not.toContain('test/model');
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
    expect(calls('POST', /\/retry$/)[0]).toMatchObject({ path: '/conversations/c1/messages/m3/retry', body: { model: 'test/model', effort: '' } });
    await answer('second reply');
    expect(turns(wrapper)).toEqual(['Q: hello', 'A: second reply']);
    expect(wrapper.find('.assistant-message .versions').text()).toContain('2 / 2');
    // No separate "Try again with…": switch the model in the composer, then Try again.
    expect(wrapper.find('.assistant-message .retry-with').exists()).toBe(false);
    await wrapper.find('.composer .model button').trigger('click');
    await flushPromises();
    [...document.body.querySelectorAll<HTMLElement>('[role="option"]')].find((o) => o.textContent?.includes('other/model'))!.click();
    await flushPromises();
    await wrapper.find('.assistant-message button[aria-label="Try again"]').trigger('click');
    await flushPromises();
    expect(calls('POST', /\/retry$/)[1].body).toEqual({ model: 'other/model', effort: '' });
    await answer('third reply');
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
    // A new chat no one has written in offers a temporary one; inside it, the button starts a new chat.
    expect(corner(wrapper)).toBe('Temporary chat');
    await wrapper.find('.chat-header .corner').trigger('click');
    await flushPromises();
    expect(corner(wrapper)).toBe('New chat');
    expect(wrapper.find('.temporary-note').exists()).toBe(true);
    await ask(wrapper, 'secret');
    expect(calls('POST', /^\/conversations$/)[0].body).toMatchObject({ temporary: true });
    await answer('kept quiet');
    expect(calls('POST', /\/title$/)).toHaveLength(0);
    expect(server.session).toBeNull();
    expect(wrapper.find('.assistant-message button[aria-label="Branch in new chat"]').exists()).toBe(false);
    await wrapper.find('.chat-header .corner').trigger('click');
    await flushPromises();
    expect(calls('DELETE', /^\/conversations\/c1$/)).toHaveLength(1);
    expect(turns(wrapper)).toEqual([]);
    expect(wrapper.find('.temporary-note').exists()).toBe(false);
    expect(corner(wrapper)).toBe('Temporary chat');
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

  it('folds a reply\'s reasoning away under one line, and shows none for a reply without it', async () => {
    const { wrapper } = await openPlayground();
    await ask(wrapper, 'think first');
    streams[0].send({ type: 'reasoning', text: 'Weigh the **options**, ' });
    streams[0].send({ type: 'reasoning', text: 'then pick one.' });
    await flushPromises();
    const reasoning = () => wrapper.find('.assistant-message .reasoning');
    expect(reasoning().find('.toggle').text()).toContain('Thinking…');
    expect(reasoning().find('.toggle').attributes('aria-expanded')).toBe('false');
    expect(reasoning().find('.body').exists()).toBe(false);
    // The reasoning has its own line: the reply's own line says only that it works on, with its clock.
    expect(wrapper.find('.assistant-message .run-status .label').text()).toBe('Working…');
    expect(wrapper.find('.assistant-message .run-status .time').text()).toBe('0s');
    await answer('Option two.', { reasoning: 'Weigh the **options**, then pick one.', reasoning_ms: 4200 });
    expect(reasoning().find('.toggle').text()).toContain('Thought for 4.2 s');
    expect(reasoning().find('.body').exists()).toBe(false);
    await reasoning().find('.toggle').trigger('click');
    expect(reasoning().find('.toggle').attributes('aria-expanded')).toBe('true');
    expect(reasoning().find('.body').text()).toBe('Weigh the options, then pick one.');
    expect(reasoning().find('.body strong').text()).toBe('options');
    expect(wrapper.find('.assistant-message .content').text()).toBe('Option two.');
    // A reply from a model that shows no reasoning has no such line.
    await ask(wrapper, 'and now?');
    await answer('Plainly.');
    expect(wrapper.findAll('.assistant-message').at(-1)!.find('.reasoning').exists()).toBe(false);
    wrapper.unmount();
  });

  it('shows a reply that used tools step by step, asks before a command, and lists what keeps running', async () => {
    state.tools = ['read', 'shell', 'terminal'];
    const { wrapper } = await openPlayground();
    // The tools Settings turned on, all on for a new chat; one turned off here goes with the chat.
    expect(wrapper.find('.tools-menu').text()).toContain('3');
    await ask(wrapper, 'look around');
    const stream = streams[0];
    stream.send({ type: 'delta', text: 'Let me look.' });
    const call = { type: 'tool', id: 'call_1', name: 'shell', arguments: '', status: 'pending', output: '', exit_code: null, ms: 0, meta: {}, approval: '', round: 0 };
    stream.send({ type: 'tool', step: call });
    await flushPromises();
    const tool = () => wrapper.find('.assistant-message .tool-call');
    expect(tool().find('.state').text()).toBe('Preparing…');
    const asked = { ...call, arguments: '{"command": "ls -la"}', status: 'waiting', approval: 'command' };
    stream.send({ type: 'tool', step: asked });
    await flushPromises();
    expect([tool().find('.verb').text(), tool().find('.detail').text()]).toEqual(['Run', 'ls -la']);
    expect(tool().find('.approval').text()).toContain('Run this command?');
    // A call that waits stands open, with what it would run.
    expect(tool().find('.command').text()).toBe('$ ls -la');
    await tool().findAll('.answers md-filled-button, .answers button').at(-1)!.trigger('click');
    await flushPromises();
    expect(calls('POST', /\/approvals\/call_1$/).map((c) => c.body)).toEqual([{ decision: 'allow' }]);
    const done = { ...asked, status: 'done', approval: 'command', output: 'total 0', exit_code: 0, ms: 1200 };
    stream.send({ type: 'tool', step: { ...asked, status: 'running' } });
    stream.send({ type: 'tool', step: done });
    await flushPromises();
    expect(tool().find('.approval').exists()).toBe(false);
    expect(tool().find('.state').text()).toBe('1.2 s');
    // Opened to ask, it folds again once done; it opens on a press.
    expect(tool().find('.output').exists()).toBe(false);
    await tool().find('.line').trigger('click');
    expect(tool().find('.output').text()).toBe('total 0');
    expect(wrapper.find('.assistant-message .run-status .label').text()).toBe('Working…');
    server.terminals = [{ id: 3, command: 'npm run dev', cwd: '/w', started_at: 1, running: true, exit_code: null }];
    await answer('All good.', { steps: [{ type: 'text', text: 'Let me look.', round: 0 }, done, { type: 'text', text: 'All good.', round: 1 }] });
    expect(wrapper.findAll('.assistant-message .content').map((c) => c.text())).toEqual(['Let me look.', 'All good.']);
    expect(wrapper.find('.assistant-message .run-status').exists()).toBe(false);
    // What a tool left running sits over the composer, with a way to stop it.
    expect(wrapper.find('.terminals .command').text()).toBe('npm run dev');
    await wrapper.find('.terminals md-icon-button, .terminals button').trigger('click');
    await flushPromises();
    expect(calls('DELETE', /\/terminals\/3$/)).toHaveLength(1);
    expect(wrapper.find('.terminals').exists()).toBe(false);
    state.tools = [];
    wrapper.unmount();
  });

  it('steers a reply with tools while it runs, and gives back what it never read', async () => {
    state.tools = ['shell'];
    const { wrapper } = await openPlayground();
    await ask(wrapper, 'work on it');
    server.steerOpen = true;
    const textarea = wrapper.find('.composer textarea');
    expect(textarea.attributes('placeholder')).toBe('Add to what it’s doing…');
    await textarea.setValue('use the other file');
    await textarea.trigger('keydown', { key: 'Enter' });
    await flushPromises();
    const sent = calls('POST', /\/steer$/);
    expect(sent.map((c) => c.body)).toEqual([{ content: 'use the other file' }]);
    expect((textarea.element as HTMLTextAreaElement).value).toBe('');
    expect(wrapper.find('.queued').text()).toContain('use the other file');
    expect(wrapper.find('.queued').text()).toContain('Read after the current step');
    // The model reads it at its next round: it moves into the reply, where it was read.
    streams[0].send({ type: 'delta', text: 'Starting.' });
    streams[0].send({ type: 'steer', step: { type: 'user', id: 'steer' + (server.ids), text: 'use the other file', round: 1 } });
    await flushPromises();
    expect(wrapper.find('.queued').exists()).toBe(false);
    expect(wrapper.find('.assistant-message .steered').text()).toBe('use the other file');
    // Another one, never read before the stop, comes back to the box.
    await textarea.setValue('and hurry');
    await textarea.trigger('keydown', { key: 'Enter' });
    await flushPromises();
    await wrapper.find('.composer button[aria-label="Stop"]').trigger('click');
    await flushPromises();
    expect((textarea.element as HTMLTextAreaElement).value).toBe('and hurry');
    // Once nothing takes a message, the text stays in the box with a word why.
    server.steerOpen = false;
    await ask(wrapper, 'again');
    await textarea.setValue('late words');
    await textarea.trigger('keydown', { key: 'Enter' });
    await flushPromises();
    expect((textarea.element as HTMLTextAreaElement).value).toBe('late words');
    expect(state.show).toHaveBeenCalledWith('The reply had already ended; your message is back in the box.');
    state.tools = [];
    wrapper.unmount();
  });

  it('lists every tool in the composer, each with a switch for this chat', async () => {
    state.tools = ['read', 'shell'];
    const { wrapper } = await openPlayground();
    expect(wrapper.find('.tools-menu').text()).toContain('2');
    await wrapper.find('.tools-menu button').trigger('click');
    await flushPromises();
    const switches = () => [...document.body.querySelectorAll<HTMLElement>('.menu [role="switch"]')];
    expect(switches().map((s) => [s.querySelector('.name')!.textContent, s.getAttribute('aria-checked')])).toEqual([
      ['Read files', 'true'], ['Edit files', 'false'], ['Run commands', 'true'], ['Background commands', 'false'], ['Search the web', 'false'], ['Read web pages', 'false'],
    ]);
    // On a new chat, one Settings leaves off is turned on, one it turns on is turned off.
    switches()[4].click();
    switches()[2].click();
    await flushPromises();
    expect(switches().map((s) => s.getAttribute('aria-checked'))).toEqual(['true', 'false', 'false', 'false', 'true', 'false']);
    await ask(wrapper, 'look it up');
    await answer('Found.');
    expect(calls('POST', /^\/conversations$/)[0].body).toMatchObject({ tools_on: ['web_search'], tools_off: ['shell'] });
    // In a chat that exists, the change is kept with it.
    await wrapper.find('.tools-menu button').trigger('click');
    await flushPromises();
    switches()[1].click();
    await flushPromises();
    expect(calls('PATCH', /^\/conversations\/c\d+$/).map((c) => c.body)).toContainEqual({ tools_on: ['apply_patch', 'web_search'], tools_off: ['shell'] });
    state.tools = [];
    wrapper.unmount();
  });

  it('sets when the chat\'s tools ask first from a menu in the composer', async () => {
    state.tools = ['shell'];
    const { wrapper } = await openPlayground();
    const chip = () => wrapper.find('.approval-menu button');
    // A new chat starts as Settings say.
    expect(chip().text()).toBe('Ask');
    await chip().trigger('click');
    await flushPromises();
    const options = () => [...document.body.querySelectorAll<HTMLElement>('.menu [role="option"]')];
    expect(options().map((o) => [o.querySelector('.name')!.textContent, o.getAttribute('aria-selected')])).toEqual([
      ['Ask first', 'true'], ['Ask outside the workspace', 'false'], ['Full access', 'false'],
    ]);
    options()[2].click();
    await flushPromises();
    expect(chip().text()).toBe('Full access');
    expect(chip().classes()).toContain('danger');
    // A new chat is created with it; a chat that exists keeps the change.
    await ask(wrapper, 'go');
    await answer('Went.');
    expect((calls('POST', /^\/conversations$/)[0].body as { approval: string }).approval).toBe('full');
    await chip().trigger('click');
    await flushPromises();
    options()[1].click();
    await flushPromises();
    expect(calls('PATCH', /^\/conversations\/c\d+$/).map((c) => c.body)).toContainEqual({ approval: 'auto' });
    expect(chip().text()).toBe('Auto');
    // The next new chat starts as Settings say again.
    await wrapper.find('.chat-header .corner').trigger('click');
    expect(chip().text()).toBe('Ask');
    state.tools = [];
    wrapper.unmount();
  });

  it('says a short think was a moment', async () => {
    const { wrapper } = await openPlayground();
    await ask(wrapper, 'quick');
    streams[0].send({ type: 'reasoning', text: 'Easy.' });
    await answer('Done.', { reasoning: 'Easy.', reasoning_ms: 300 });
    expect(wrapper.find('.reasoning .toggle').text()).toContain('Thought for a moment');
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
    expect(wrapper.find('.composer').text()).toContain('other/model');
    await ask(wrapper, 'more');
    expect(calls('POST', /\/messages$/)[0]).toMatchObject({ path: '/conversations/old/messages', body: { parent_id: 'o2', content: 'more', model: 'other/model' } });
    wrapper.unmount();
  });

  it('starts afresh when opened to try one model, and lets the address go', async () => {
    server.session = 'old';
    const { wrapper, router } = await openPlayground('/playground?model=other/model');
    expect(calls('GET', /^\/session/)).toHaveLength(0);
    expect(wrapper.find('.composer').text()).toContain('other/model');
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

  it('starts a new chat from the header, opens and closes the chat list, and recalls the last question for editing', async () => {
    const { wrapper } = await openPlayground();
    await ask(wrapper, 'hello');
    await answer('hi');
    await wrapper.find('.composer textarea').trigger('keydown', { key: 'ArrowUp' });
    await flushPromises();
    expect(wrapper.find('.user-message .editor').exists()).toBe(true);
    await wrapper.find('.chat-header .corner').trigger('click');
    await flushPromises();
    expect(turns(wrapper)).toEqual([]);
    expect(server.session).toBeNull();
    const toggle = () => wrapper.find('.chat-header .list-toggle');
    expect(wrapper.find('.sidebar').exists()).toBe(true);
    expect(toggle().attributes()).toMatchObject({ 'aria-expanded': 'true', 'aria-label': 'Close sidebar' });
    await toggle().trigger('click');
    await flushPromises();
    expect(wrapper.find('.sidebar').exists()).toBe(false);
    expect(toggle().attributes()).toMatchObject({ 'aria-expanded': 'false', 'aria-label': 'Open sidebar' });
    wrapper.unmount();
  });
});

describe('Playground commands, compaction and run time', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    server = { conversations: new Map(), session: null, calls: [], ids: 0, clock: 0 };
    streams = [];
    state.route.mockImplementation(handle);
    vi.stubGlobal('fetch', state.fetch);
    localStorage.clear();
  });
  afterEach(() => {
    vi.unstubAllGlobals();
    document.body.innerHTML = '';
  });

  const menu = () => document.querySelector<HTMLElement>('.slash-menu');
  const rows = () => [...document.querySelectorAll<HTMLElement>('.slash-menu [data-row]')];
  const names = () => rows().map((r) => r.querySelector('.name, .name-text')?.textContent?.trim());
  async function type(wrapper: Wrapper, text: string) {
    await wrapper.find('.composer textarea').setValue(text);
    await flushPromises();
  }
  async function key(wrapper: Wrapper, name: string) {
    await wrapper.find('.composer textarea').trigger('keydown', { key: name });
    await flushPromises();
  }

  it('opens the commands on a slash, narrows them as the name is typed, and runs one with Enter', async () => {
    const { wrapper } = await openPlayground();
    await type(wrapper, '/');
    expect(menu()).not.toBeNull();
    expect(names()).toEqual(['/model', '/effort', '/compact', '/status', '/new', '/fork', '/tools', '/permissions', '/ps', '/stop']);
    expect(wrapper.find('.composer textarea').attributes('aria-expanded')).toBe('true');
    // Commands with nothing to work on yet say why.
    expect(rows()[2].getAttribute('aria-disabled')).toBe('true');
    await type(wrapper, '/co');
    expect(names()).toEqual(['/compact', '/status', '/ps', '/stop']);
    expect(rows()[0].querySelector('b')?.textContent).toBe('co');
    await key(wrapper, 'ArrowDown');
    expect(rows()[1].getAttribute('aria-selected')).toBe('true');
    expect(wrapper.find('.composer textarea').attributes('aria-activedescendant')).toBe(rows()[1].id);
    // Tab completes the name; a command that lists opens its list.
    await key(wrapper, 'Tab');
    expect((wrapper.find('.composer textarea').element as HTMLTextAreaElement).value).toBe('/status ');
    expect(document.querySelector('.slash-menu .status-panel')?.textContent).toContain('test/model');
    await type(wrapper, '/new');
    await key(wrapper, 'Enter');
    expect(menu()).toBeNull();
    expect((wrapper.find('.composer textarea').element as HTMLTextAreaElement).value).toBe('');
    // Nothing went out as a message.
    expect(calls('POST', /^\/conversations/)).toEqual([]);
    wrapper.unmount();
  });

  it('chooses a model from /model, narrowed by the words after it', async () => {
    const { wrapper } = await openPlayground();
    await type(wrapper, '/model ');
    expect(names()).toEqual(['test/model', 'other/model']);
    expect(rows()[0].getAttribute('aria-selected')).toBe('true');
    await type(wrapper, '/model other');
    expect(names()).toEqual(['other/model']);
    await key(wrapper, 'Enter');
    expect(menu()).toBeNull();
    expect(wrapper.find('.model').text()).toContain('other/model');
    wrapper.unmount();
  });

  it('lists and filters background commands during a reply, then stops them without sending a message', async () => {
    const { wrapper } = await openPlayground();
    await ask(wrapper, 'run a server');
    server.terminals = [
      { id: 3, command: 'npm run dev', cwd: '/work/app', started_at: Date.now() / 1000, running: true, exit_code: null },
      { id: 4, command: 'npm test', cwd: '/work/tests', started_at: 1, running: false, exit_code: 1 },
    ];
    await type(wrapper, '/ps');
    expect(rows()[0].getAttribute('aria-disabled')).toBeNull();
    await key(wrapper, 'Enter');
    expect(menu()?.textContent).toContain('npm run dev');
    expect(menu()?.textContent).toContain('Exited · code 1');
    await type(wrapper, '/ps /work/tests');
    expect(menu()?.textContent).not.toContain('npm run dev');
    expect(menu()?.textContent).toContain('npm test');
    // Enter opens /stop, which always shows every running command, regardless of the filter.
    await key(wrapper, 'Enter');
    expect((wrapper.find('.composer textarea').element as HTMLTextAreaElement).value).toBe('/stop ');
    expect(menu()?.textContent).toContain('npm run dev');
    expect(menu()?.textContent).not.toContain('npm test');
    expect(calls('DELETE', /\/terminals$/)).toHaveLength(0);
    await key(wrapper, 'Enter');
    expect(calls('DELETE', /\/terminals$/)).toHaveLength(1);
    expect(menu()?.textContent).toContain('Background commands stopped.');
    expect(menu()?.textContent).not.toContain('npm run dev');
    expect(streams[0].signal.aborted).toBe(false);
    expect(calls('POST', /\/messages$/)).toHaveLength(1);
    await key(wrapper, 'Enter');
    expect(calls('DELETE', /\/terminals$/)).toHaveLength(1);
    await key(wrapper, 'Escape');
    expect(menu()).toBeNull();
    wrapper.unmount();
  });

  it('shows an empty command pane for a new chat without creating a conversation', async () => {
    const { wrapper } = await openPlayground();
    await type(wrapper, '/stop ');
    expect(menu()?.textContent).toContain('No background commands.');
    await key(wrapper, 'Enter');
    expect(calls('DELETE', /\/terminals/)).toHaveLength(0);
    expect(calls('GET', /\/terminals/)).toHaveLength(0);
    expect(calls('POST', /^\/conversations/)).toHaveLength(0);
    wrapper.unmount();
  });

  it('keeps commands visible on a failed stop and allows refreshing and retrying', async () => {
    const { wrapper } = await openPlayground();
    await ask(wrapper, 'run');
    await answer('running');
    server.terminals = [{ id: 3, command: 'npm run dev', cwd: '/work', started_at: 1, running: true, exit_code: null }];
    await type(wrapper, '/stop ');
    state.route.mockImplementationOnce(() => json({ message: 'Could not stop commands' }, 500));
    await key(wrapper, 'Enter');
    expect(menu()?.querySelector('[role="alert"]')?.textContent).toBe('Could not stop commands');
    expect(menu()?.textContent).toContain('npm run dev');
    expect(menu()?.textContent).not.toContain('Background commands stopped.');
    menu()?.querySelector<HTMLElement>('[aria-label="Refresh"]')?.click();
    await flushPromises();
    await key(wrapper, 'Enter');
    expect(menu()?.textContent).toContain('Background commands stopped.');
    wrapper.unmount();
  });

  it('refreshes an open list and discards a late response after switching to a new chat', async () => {
    const { wrapper } = await openPlayground();
    await ask(wrapper, 'run');
    await answer('running');
    const terminal = { id: 3, command: 'npm run dev', cwd: '/work', started_at: 1, running: true, exit_code: null };
    server.terminals = [terminal];
    await type(wrapper, '/ps ');
    vi.useFakeTimers();
    try {
      // Manual refresh schedules the next poll on this clock.
      menu()?.querySelector<HTMLElement>('[aria-label="Refresh"]')?.click();
      await flushPromises();
      server.terminals = [{ ...terminal, running: false, exit_code: 0 }];
      await vi.advanceTimersByTimeAsync(2000);
      await flushPromises();
      expect(menu()?.textContent).toContain('Exited · code 0');
      let finish!: (response: Response) => void;
      state.route.mockImplementationOnce(() => new Promise<Response>((resolve) => { finish = resolve; }));
      menu()?.querySelector<HTMLElement>('[aria-label="Refresh"]')?.click();
      await flushPromises();
      await wrapper.find('.chat-header .corner').trigger('click');
      await flushPromises();
      await type(wrapper, '/ps ');
      finish(json({ terminals: [terminal] }));
      await flushPromises();
      expect(menu()?.textContent).toContain('No background commands.');
      expect(menu()?.textContent).not.toContain('npm run dev');
    } finally {
      wrapper.unmount();
      vi.useRealTimers();
    }
  });

  it('closes on Escape until the command changes, and a closed command still runs rather than going out', async () => {
    const { wrapper } = await openPlayground();
    await type(wrapper, '/fork');
    await key(wrapper, 'Escape');
    expect(menu()).toBeNull();
    await type(wrapper, '/fork');
    expect(menu()).toBeNull();
    await key(wrapper, 'Enter');
    expect(state.show).toHaveBeenCalledWith('Nothing to work with yet: send a message first');
    expect(calls('POST', /^\/conversations/)).toEqual([]);
    await type(wrapper, '/for');
    expect(menu()).not.toBeNull();
    // Closed, then typed again from scratch: it opens again.
    await type(wrapper, '/status ');
    await key(wrapper, 'Escape');
    expect(menu()).toBeNull();
    await type(wrapper, '');
    await type(wrapper, '/status ');
    expect(document.querySelector('.slash-menu .status-panel')).not.toBeNull();
    // A path is a message, not a command.
    await type(wrapper, '/etc/hosts');
    expect(menu()).toBeNull();
    wrapper.unmount();
  });

  it('compacts with /compact, shows the checkpoint, and opens it to the summary', async () => {
    const { wrapper } = await openPlayground();
    await ask(wrapper, 'hello');
    await answer('Hi there.');
    await type(wrapper, '/compact keep the names');
    await key(wrapper, 'Enter');
    expect(calls('POST', /\/compact$/)[0].body).toEqual({ instructions: 'keep the names' });
    expect(wrapper.find('.compaction').text()).toContain('Compacting context…');
    const c = server.conversations.get('c1')!;
    const checkpoint = c.messages.find((m) => m.role === 'checkpoint')!;
    const record = { ...(checkpoint.steps![0] as object), status: 'done', summary: '## Goal\nSay hello.', recent: ['hello'], tokens_before: 118_000, tokens_after: 14_500, model: 'test/model', ms: 8200 };
    Object.assign(checkpoint, { status: 'complete', content: '## Goal\nSay hello.', steps: [record] });
    streams[streams.length - 1].send({ type: 'done', message: checkpoint });
    streams[streams.length - 1].close();
    await flushPromises();
    const marker = wrapper.find('.compaction');
    expect(marker.text()).toContain('Context compacted');
    expect(marker.text()).toContain('118K → 14.5K tokens');
    expect(state.show).toHaveBeenCalledWith('Context compacted');
    await marker.find('button.head').trigger('click');
    expect(marker.find('.summary').text()).toContain('Say hello.');
    expect(marker.find('.recent').text()).toBe('hello');
    // The next question follows the checkpoint.
    await ask(wrapper, 'and then?');
    expect((calls('POST', /\/messages$/)[1].body as { parent_id: string }).parent_id).toBe(checkpoint.id);
    wrapper.unmount();
  });

  it('shows what a reply is doing and for how long, a compaction inside it, and how long it worked', async () => {
    const { wrapper } = await openPlayground();
    await ask(wrapper, 'go');
    const status = () => wrapper.find('.assistant-message .run-status');
    expect(status().find('.label').text()).toBe('Answering…');
    expect(status().find('.time').text()).toBe('0s');
    const step = { type: 'compaction', id: 'cmp1', status: 'running', trigger: 'auto', phase: 'pre-turn', round: 0, summary: '', recent: [], tokens_before: 0, tokens_after: 0, model: '', ms: 0, error: '', at: Date.now() / 1000 };
    streams[0].send({ type: 'compaction', step });
    await flushPromises();
    expect(status().find('.label').text()).toBe('Compacting context…');
    expect(wrapper.find('.assistant-message .compaction').text()).toContain('automatically, near the context limit');
    streams[0].send({ type: 'compaction', step: { ...step, status: 'done', summary: 'S', tokens_before: 140_000, tokens_after: 9_000 } });
    streams[0].send({ type: 'usage', usage: { context: 9_100, source: 'usage' }, window: 131_072, limit: 111_411 });
    await flushPromises();
    expect(wrapper.find('.assistant-message .compaction').text()).toContain('140K → 9K tokens');
    server.context = {
      model: 'test/model', provider: 'test', effort: '', window: 131_072, window_source: 'catalog', limit: 111_411, headroom: 13_107, used: 9_100, used_source: 'usage',
      auto_compact: true, last: { input: 9_000, cached: 4_000, output: 100, reasoning: 0 }, totals: { input: 9_000, cached: 4_000, output: 100, reasoning: 0 }, rounds: 1, replies: 1,
      compactions: 1, last_compaction: null, last_duration_ms: 83_000, total_duration_ms: 83_000, running_since: null, tools: [], approval: 'ask',
    };
    await answer('Done.', { steps: [step, { type: 'text', text: 'Done.', round: 0 }], duration_ms: 83_000, finished_at: 1 });
    expect(status().exists()).toBe(false);
    expect(wrapper.find('.assistant-message .worked').text()).toBe('Worked for 1m 23s');
    // How full the context is: a ring beside the send button, opening the status.
    const ring = wrapper.find('.context-ring');
    expect(ring.text()).toBe('93%');
    await ring.trigger('click');
    await flushPromises();
    const panel = document.querySelector('.slash-menu .status-panel')!;
    expect(panel.textContent).toContain('9.1K of 131K tokens used');
    expect(panel.textContent).toContain('4K cached');
    expect(panel.textContent).toContain('Last reply 1m 23s');
    // The ring leaves the box alone, and opens the status as often as it is pressed.
    const box = () => (wrapper.find('.composer textarea').element as HTMLTextAreaElement).value;
    expect(box()).toBe('');
    await ring.trigger('click');
    await flushPromises();
    expect(menu()).toBeNull();
    await ring.trigger('click');
    await flushPromises();
    expect(menu()).not.toBeNull();
    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }));
    await flushPromises();
    expect(menu()).toBeNull();
    await ring.trigger('click');
    await flushPromises();
    expect(document.querySelector('.slash-menu .status-panel')).not.toBeNull();
    // Typing gives the box back: the status closes, and the draft is the user's.
    await type(wrapper, 'hello');
    expect(menu()).toBeNull();
    expect(box()).toBe('hello');
    wrapper.unmount();
  });
});

