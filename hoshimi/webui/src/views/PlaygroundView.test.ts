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
vi.mock('@/ui', () => {
  const slot = { template: '<div><slot /></div>' };
  return {
    icons: {}, SelectField: slot, PickerMenu: slot, TRANSITIONS: { list: 'list' }, Skeleton: slot, EmptyState: slot,
    FieldRow: { template: '<div><slot /><slot name="end" /></div>' },
    AppButton: { props: ['disabled'], template: '<button :disabled="disabled" @click="$emit(\'click\')"><slot /></button>' },
    IconButton: { props: ['label', 'disabled'], template: '<button :aria-label="label" :disabled="disabled" @click="$emit(\'click\')" />' },
    useSnackbar: () => ({ error: state.error }),
  };
});

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
    expect(wrapper.text()).toContain('Stop');
    emit('，第二段');
    await flushPromises();
    expect(wrapper.find('.assistant .bubble').text()).toBe('第一段，第二段');
    source.enqueue(new TextEncoder().encode('data: [DONE]\n\n'));
    await flushPromises();
    expect(wrapper.text()).toContain('Send');
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
    await wrapper.findAll('button').find((button) => button.text() === 'Stop')!.trigger('click');
    await flushPromises();
    expect(signal.aborted).toBe(true);
    expect(wrapper.find('.assistant .bubble').text()).toBe('partial');
    expect(state.error).not.toHaveBeenCalled();
    expect(wrapper.text()).toContain('Send');
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
    expect(wrapper.text()).toContain('Send');
    wrapper.unmount();
  });

  it('clears an active reply and cancels its request', async () => {
    const wrapper = await openPlayground();
    await wrapper.find('textarea').setValue('hello');
    await wrapper.find('textarea').trigger('keydown', { key: 'Enter' });
    await flushPromises();
    emit('partial');
    await flushPromises();
    await wrapper.find('button[aria-label]').trigger('click');
    await flushPromises();
    expect(signal.aborted).toBe(true);
    expect(wrapper.findAll('.turn')).toHaveLength(0);
    expect(state.error).not.toHaveBeenCalled();
    wrapper.unmount();
  });
});
