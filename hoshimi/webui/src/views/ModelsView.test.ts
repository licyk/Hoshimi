import { flushPromises, mount } from '@vue/test-utils';
import { QueryClient, VueQueryPlugin } from '@tanstack/vue-query';
import { createPinia } from 'pinia';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { createMemoryHistory, createRouter } from 'vue-router';
import { SelectField } from '@/ui';
import ModelsView from '@/views/ModelsView.vue';

vi.mock('@/api/queries/app', async (original) => ({
  ...await original<typeof import('@/api/queries/app')>(),
  useProviders: () => ({ data: { value: [] } }),
  useProbe: () => ({ data: { value: { running: false } } }),
  useStartProbe: () => ({ isPending: { value: false }, mutateAsync: vi.fn() }),
}));

const model = (id: string, provider: string, context: number) => ({
  id, provider, name: id, wire: 'chat', vision: provider === 'openai', reasoning: false, tools: null,
  context_window: context, max_output: 0, free: null, efforts: [], probe: null, unavailable: false,
});
const providers = [
  { id: 'buddy', status: 'ready', data: ['glm-5.3', 'glm-5.3-flash'].map((id) => model(id, 'buddy', 32000)) },
  { id: 'openai', status: 'ready', data: ['glm-5.3', 'glm-5.3-flash', 'glm-5-turbo'].map((id) => model(id, 'openai', 1048576)) },
];

describe('models from overlapping channels', () => {
  afterEach(() => vi.unstubAllGlobals());

  it('lists every channel, filters all three OpenAI models, and uses their own capabilities and route', async () => {
    const body = [{ type: 'snapshot', levels: [], providers }, { type: 'done' }]
      .map((event) => `data: ${JSON.stringify(event)}\n\n`).join('');
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(body)));
    const writeText = vi.fn().mockResolvedValue(undefined);
    vi.stubGlobal('navigator', { ...navigator, clipboard: { writeText } });
    const router = createRouter({ history: createMemoryHistory(), routes: [
      { path: '/models', component: ModelsView }, { path: '/playground', component: { template: '<div />' } },
    ] });
    await router.push('/models');
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const w = mount(ModelsView, { global: { plugins: [router, createPinia(), [VueQueryPlugin, { queryClient: qc }]], stubs: { CatalogStatus: true } } });
    try {
      await vi.waitFor(() => expect(w.findAll('.row.body')).toHaveLength(5));
      const table = w.findComponent({ name: 'DataTable' });
      const keys = table.props('rows').map(table.props('rowKey'));
      expect(new Set(keys).size).toBe(5);
      const filter = w.findAllComponents(SelectField).find((field) => field.props('label') === 'Provider')!;
      filter.vm.$emit('update:modelValue', 'openai');
      await flushPromises();
      expect(w.findAll('.row.body .mono').map((row) => row.text())).toEqual(['glm-5.3', 'glm-5.3-flash', 'glm-5-turbo']);
      expect(table.props('rows')).toEqual(expect.arrayContaining([
        expect.objectContaining({ id: 'glm-5.3', owner: 'openai', vision: true, context: 1048576 }),
      ]));
      const first = w.find('.row.body');
      await first.find('[aria-label="Copy the model id"]').trigger('click');
      expect(writeText).toHaveBeenCalledWith('openai/glm-5.3');
      await first.find('[aria-label="Open in the Playground"]').trigger('click');
      await flushPromises();
      expect(router.currentRoute.value.query).toEqual({ model: 'openai/glm-5.3' });
    } finally {
      w.unmount();
      qc.clear();
    }
  });
});
