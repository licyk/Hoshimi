import { flushPromises, mount } from '@vue/test-utils';
import { QueryClient, VueQueryPlugin } from '@tanstack/vue-query';
import { createPinia, setActivePinia } from 'pinia';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { defineComponent, h } from 'vue';
import { catalogSpecs, findModelSpec, modelKey, readCatalog, useCatalog, type CatalogSnapshot } from '@/api/catalog';
import { useModels, useModelSpecs } from '@/api/queries/app';
import { useAuthStore } from '@/stores/auth';
import type { S } from '@/api/types';

const model = (id: string, provider = 'fast'): S['ModelSpecObject'] => ({
  id, provider, name: id, free: null, efforts: [], wire: 'chat', vision: false, reasoning: false, tools: null,
  context_window: 0, max_output: 0, can_disable_thinking: true, region_sensitive: false, effort_default: null, probe: null, unavailable: false,
});
const event = (value: unknown, ending = '\r\n\r\n') => new TextEncoder().encode(`data: ${JSON.stringify(value)}${ending}`);
const snapshot = () => ({ type: 'snapshot', levels: [], providers: [{ id: 'slow', status: 'loading', data: [] }, { id: 'fast', status: 'loading', data: [] }] });

describe('incremental provider catalogues', () => {
  beforeEach(() => { localStorage.clear(); setActivePinia(createPinia()); });
  afterEach(() => vi.unstubAllGlobals());

  it('shares one stream between pickers and renders healthy rows while a provider is still loading', async () => {
    let source!: ReadableStreamDefaultController<Uint8Array>;
    const stream = new ReadableStream<Uint8Array>({ start: (controller) => { source = controller; } });
    const fetch = vi.fn().mockResolvedValue(new Response(stream));
    vi.stubGlobal('fetch', fetch);
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const view = defineComponent({ setup() {
      const models = useModels();
      const specs = useModelSpecs();
      const catalog = useCatalog();
      return () => h('div', [h('p', models.data.value?.data.map((m) => m.id).join(',') ?? ''), h('span', specs.data.value?.data.length ?? 0), h('b', String(catalog.isFetching.value))]);
    } });
    const wrapper = mount(view, { global: { plugins: [createPinia(), [VueQueryPlugin, { queryClient: qc }]] } });
    source.enqueue(event(snapshot()));
    source.enqueue(event({ type: 'provider', provider: { id: 'fast', status: 'ready', data: [model('星辰')] } }));
    await vi.waitFor(() => expect(wrapper.find('p').text()).toBe('fast/星辰'));
    expect(wrapper.find('b').text()).toBe('true');
    expect(wrapper.find('span').text()).toBe('1');
    expect(fetch).toHaveBeenCalledOnce();
    source.enqueue(event({ type: 'provider', provider: { id: 'slow', status: 'timeout', data: [] } }));
    source.enqueue(event({ type: 'done' }));
    await vi.waitFor(() => expect(wrapper.find('b').text()).toBe('false'));
    expect(wrapper.find('p').text()).toBe('fast/星辰');
    expect(stream.locked).toBe(false);
    wrapper.unmount();
    qc.clear();
  });

  it('handles split UTF-8 and CRLF and a final done frame without a separator', async () => {
    let source!: ReadableStreamDefaultController<Uint8Array>;
    const stream = new ReadableStream<Uint8Array>({ start: (controller) => { source = controller; } });
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(stream)));
    const update = vi.fn();
    const read = readCatalog(update);
    source.enqueue(event({ type: 'snapshot', levels: [], providers: [{ id: 'fast', status: 'loading', data: [] }] }));
    const bytes = event({ type: 'provider', provider: { id: 'fast', status: 'ready', data: [model('星辰')] } });
    const split = bytes.indexOf(0xe6) + 1;
    source.enqueue(bytes.slice(0, split));
    source.enqueue(bytes.slice(split, -1));
    await vi.waitFor(() => expect(update).toHaveBeenCalledOnce());
    source.enqueue(bytes.slice(-1));
    await vi.waitFor(() => expect(update).toHaveBeenCalledTimes(2));
    source.enqueue(event({ type: 'done' }, ''));
    source.close();
    expect((await read).providers[0].data[0].id).toBe('星辰');
    expect(stream.locked).toBe(false);
  });

  it('keeps received snapshots on premature EOF and releases the reader', async () => {
    const response = new Response(new TextDecoder().decode(event(snapshot())));
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(response));
    const update = vi.fn();
    await expect(readCatalog(update)).rejects.toMatchObject({ code: 'catalog_interrupted' });
    expect(update).toHaveBeenCalledOnce();
    expect(response.body!.locked).toBe(false);
  });

  it('keeps same-named models from each channel as their catalogues arrive', () => {
    const catalog: CatalogSnapshot = { levels: [], done: false, providers: [
      { id: 'slow', status: 'loading', data: [], error: null, last_success: null, retry_at: null, source: 'live', diagnostic: null },
      { id: 'fast', status: 'ready', data: [model('same', 'fast')], error: null, last_success: null, retry_at: null, source: 'live', diagnostic: null },
    ] };
    expect(catalogSpecs(catalog).data[0].provider).toBe('fast');
    catalog.providers[0] = { id: 'slow', status: 'ready', data: [model('same', 'slow')], error: null, last_success: null, retry_at: null, source: 'live', diagnostic: null };
    expect(catalogSpecs(catalog).data.map(modelKey)).toEqual(['slow/same', 'fast/same']);
    catalog.providers[1].data.push(model('same', 'fast'));
    expect(catalogSpecs(catalog).data.map(modelKey)).toEqual(['slow/same', 'fast/same']);
  });

  it('resolves channel-specific capabilities while preserving old bare model IDs', () => {
    const first = { ...model('glm-5.3', 'buddy'), vision: false };
    const second = { ...model('glm-5.3', 'openai'), vision: true, context_window: 1048576 };
    const nested = model('org/model', 'openai');
    const prefixed = model('openai/missing', 'buddy');
    const models = [first, second, nested, prefixed];
    expect(findModelSpec(models, 'glm-5.3')).toBe(first);
    expect(findModelSpec(models, 'openai/glm-5.3')).toBe(second);
    expect(findModelSpec(models, 'openai/org/model')).toBe(nested);
    expect(findModelSpec(models, 'org/model')).toBe(nested);
    expect(findModelSpec(models, 'openai/missing')).toBeUndefined();
    expect(modelKey(prefixed)).toBe('buddy/openai/missing');
  });

  it('sends the tool key and opens login for management authentication failures', async () => {
    useAuthStore().setToken('tool');
    const fetch = vi.fn().mockResolvedValue(new Response('{"code":"auth_required"}', { status: 401 }));
    vi.stubGlobal('fetch', fetch);
    await expect(readCatalog(vi.fn())).rejects.toMatchObject({ status: 401 });
    expect(fetch.mock.calls[0][1].headers.Authorization).toBe('Bearer tool');
    expect(useAuthStore().needed).toBe(true);
  });

  it('does not apply late updates after cancellation', async () => {
    let source!: ReadableStreamDefaultController<Uint8Array>;
    const stream = new ReadableStream<Uint8Array>({ start: (controller) => { source = controller; } });
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(stream)));
    const controller = new AbortController();
    const update = vi.fn();
    const read = readCatalog(update, controller.signal);
    await flushPromises();
    controller.abort();
    source.enqueue(event(snapshot()));
    await expect(read).rejects.toMatchObject({ name: 'AbortError' });
    expect(update).not.toHaveBeenCalled();
    expect(stream.locked).toBe(false);
  });
});
