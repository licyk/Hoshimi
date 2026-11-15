import { flushPromises, mount } from '@vue/test-utils';
import { createPinia, setActivePinia } from 'pinia';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { ref } from 'vue';
import CallPayload from '@/components/CallPayload.vue';
import CallPayloads from '@/components/CallPayloads.vue';
import { usePreferencesStore } from '@/stores/preferences';

const data = ref<Record<string, unknown> | undefined>();
const isPending = ref(false);
const error = ref<Error | null>(null);
const refetch = vi.fn();
vi.mock('@/api/queries/usage', () => ({ useCallDetail: () => ({ data, isPending, error, refetch }) }));
vi.mock('@/ui', async () => ({
  ErrorNotice: (await import('@/ui/ErrorNotice.vue')).default,
  AppButton: { template: '<button><slot /></button>' },
}));

describe('call payload details', () => {
  beforeEach(() => {
    setActivePinia(createPinia());
    usePreferencesStore().prefs.locale = 'zh-CN';
    data.value = undefined;
    isPending.value = false;
    error.value = null;
    vi.clearAllMocks();
  });

  it('distinguishes loading, historical absence and a retryable failure', async () => {
    isPending.value = true;
    const w = mount(CallPayloads, { props: { seq: 1 } });
    expect(w.text()).toContain('加载中');
    isPending.value = false;
    data.value = { id: 'old', trace: null };
    await w.vm.$nextTick();
    expect(w.text()).toContain('历史记录未采集');
    error.value = new Error('404 Not Found');
    await w.vm.$nextTick();
    expect(w.text()).toContain('404 Not Found');
    expect(w.text()).not.toContain('历史记录未采集');
    await w.find('button').trigger('click');
    expect(refetch).toHaveBeenCalledOnce();
  });

  it('shows all attempts, client output and incomplete model output separately', () => {
    data.value = { id: 'one', trace: {
      request: { model: 'model-a' }, response: { chunks: [], complete: false },
      client_request: { method: 'POST', body: '{"messages":[]}', complete: true },
      client_response: { status: 200, body: 'data: {"text":"你好"}\n\n', complete: false },
      upstream: [{ account: 'a1', error: 'ConnectError' }, { account: 'a2', response: { status: 200 } }],
    } };
    const w = mount(CallPayloads, { props: { seq: 1 } });
    expect(w.findAll('details')).toHaveLength(6);
    expect(w.text()).toContain('客户端 HTTP 请求');
    expect(w.text()).toContain('客户端 HTTP 返回');
    expect(w.text()).toContain('上游请求与返回 2');
    expect(w.text()).toContain('a1');
    expect(w.text()).toContain('a2');
    expect(w.findAll('.partial')).toHaveLength(2);
    expect(w.text()).not.toContain('历史记录未采集');
  });

  it('renders SSE bodies as text, preserving newlines and never executing markup', async () => {
    const body = 'data: <script>alert(1)</script>\n\ndata: [DONE]\n\n';
    const writeText = vi.fn().mockResolvedValue(undefined);
    vi.stubGlobal('navigator', { clipboard: { writeText } });
    const w = mount(CallPayload, { props: { title: 'response', value: { status: 200, body } } });
    expect(w.find('script').exists()).toBe(false);
    expect(w.find('pre').element.textContent).toContain(body);
    await w.find('button').trigger('click');
    await flushPromises();
    expect(writeText.mock.calls[0][0]).toContain(body);
    expect(w.text()).toContain('已复制');
    vi.unstubAllGlobals();
  });

  it('reports clipboard failure without claiming success', async () => {
    vi.stubGlobal('navigator', { clipboard: { writeText: vi.fn().mockRejectedValue(new Error('denied')) } });
    const w = mount(CallPayload, { props: { title: 'request', value: { input: 'hello' } } });
    await w.find('button').trigger('click');
    await flushPromises();
    expect(w.text()).toContain('复制失败');
    expect(w.text()).not.toContain('已复制');
    vi.unstubAllGlobals();
  });
});
