import { mount } from '@vue/test-utils';
import { createPinia, setActivePinia } from 'pinia';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { ref } from 'vue';
import CatalogFailure from '@/components/CatalogFailure.vue';
import CatalogStatus from '@/components/CatalogStatus.vue';
import { usePreferencesStore } from '@/stores/preferences';

const catalog = ref({ providers: [] as Record<string, unknown>[] });
vi.mock('@/api/catalog', () => ({ useCatalog: () => ({ data: catalog, isFetching: ref(false), isError: ref(false), refetch: vi.fn() }) }));
vi.mock('@/ui', async () => ({
  ErrorNotice: (await import('@/ui/ErrorNotice.vue')).default,
  AppButton: { template: '<button><slot /></button>' },
}));

describe('catalogue failure explanations', () => {
  beforeEach(() => {
    setActivePinia(createPinia());
    usePreferencesStore().prefs.locale = 'zh-CN';
    catalog.value = { providers: [] };
  });

  it('shows the endpoint, status and manual-model remedy', () => {
    const w = mount(CatalogFailure, { props: { kind: 'catalog_endpoint_missing', status: 404, host: 'relay.example' } });
    expect(w.text()).toContain('HTTP 404');
    expect(w.text()).toContain('relay.example');
    expect(w.text()).toContain('API 地址');
    expect(w.text()).toContain('手动填写账号的模型 ID');
  });

  it('explains why an HTTP 200 catalogue can fail', () => {
    const w = mount(CatalogFailure, { props: { kind: 'invalid_response', status: 200 } });
    expect(w.text()).toContain('HTTP 200');
    expect(w.text()).toContain('未返回有效的模型列表');
  });

  it('shows each failed provider with its diagnostic and explains timeouts without an error code', () => {
    catalog.value.providers = [
      { id: 'openai', status: 'failed', error: 'authentication', diagnostic: { status: 401, host: 'relay.example' } },
      { id: 'slow', status: 'timeout', error: null },
    ];
    const w = mount(CatalogStatus);
    expect(w.text()).toContain('openai');
    expect(w.text()).toContain('HTTP 401');
    expect(w.text()).toContain('API 密钥');
    expect(w.text()).toContain('slow');
    expect(w.text()).toContain('连接超时');
    expect(w.text()).toContain('网络出口');
  });
});
