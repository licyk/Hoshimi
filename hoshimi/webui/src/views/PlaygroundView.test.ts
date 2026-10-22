import { flushPromises, mount } from '@vue/test-utils';
import { createPinia } from 'pinia';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { createMemoryHistory, createRouter } from 'vue-router';
import PlaygroundView from '@/views/PlaygroundView.vue';

const state = vi.hoisted(() => ({ error: vi.fn() }));
vi.mock('@/api/queries/app', () => ({
  useModels: () => ({ data: { value: { data: [{ id: 'test/model' }] } }, isPending: { value: false }, isFetching: { value: false } }),
  useModelSpecs: () => ({ data: { value: { data: [] } } }),
}));
vi.mock('@/ui', async (importOriginal) => ({ ...(await importOriginal<typeof import('@/ui')>()), useSnackbar: () => ({ error: state.error }) }));

async function openPlayground() {
  const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/playground', component: PlaygroundView }] });
  await router.push('/playground');
  return mount(PlaygroundView, { global: { plugins: [router, createPinia()], stubs: { CatalogStatus: true } } });
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

  it('clears an active reply and cancels its request', async () => {
    const wrapper = await openPlayground();
    await wrapper.find('textarea').setValue('hello');
    await wrapper.find('textarea').trigger('keydown', { key: 'Enter' });
    await flushPromises();
    emit('partial');
    await flushPromises();
    await wrapper.find('button[aria-label="Clear the conversation"]').trigger('click');
    await flushPromises();
    expect(signal.aborted).toBe(true);
    expect(wrapper.findAll('.turn')).toHaveLength(0);
    expect(state.error).not.toHaveBeenCalled();
    wrapper.unmount();
  });

  it('keeps every control inside the composer, with no row of controls above the thread', async () => {
    const wrapper = await openPlayground();
    const composer = wrapper.find('.composer');
    expect(composer.find('textarea').exists()).toBe(true);
    expect(composer.find('button[aria-label^="Model"]').exists()).toBe(true);
    expect(composer.find('button[aria-label="Send"]').attributes('disabled')).toBeDefined();
    // Nothing to clear yet: Clear arrives with the first message.
    expect(wrapper.find('button[aria-label="Clear the conversation"]').exists()).toBe(false);
    await wrapper.find('textarea').setValue('hello');
    expect(composer.find('button[aria-label="Send"]').attributes('disabled')).toBeUndefined();
    await wrapper.find('textarea').trigger('keydown', { key: 'Enter', shiftKey: true });
    await flushPromises();
    expect(globalThis.fetch).not.toHaveBeenCalled();
    await composer.find('button[aria-label="Send"]').trigger('click');
    await flushPromises();
    expect(globalThis.fetch).toHaveBeenCalledOnce();
    expect(wrapper.find('.composer button[aria-label="Clear the conversation"]').exists()).toBe(true);
    wrapper.unmount();
  });
});
