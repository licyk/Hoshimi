import { enableAutoUnmount, flushPromises, mount } from '@vue/test-utils';
import { createPinia, setActivePinia } from 'pinia';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ref } from 'vue';
import CallPayload from '@/components/CallPayload.vue';
import CallPayloads from '@/components/CallPayloads.vue';
import { usePreferencesStore } from '@/stores/preferences';
import { AppButton, ExpansionPanel, useSnackbar } from '@/ui';

const data = ref<Record<string, unknown> | undefined>();
const isPending = ref(false);
const error = ref<Error | null>(null);
const refetch = vi.fn();
vi.mock('@/api/queries/usage', () => ({ useCallDetail: () => ({ data, isPending, error, refetch }) }));
enableAutoUnmount(afterEach);
afterEach(() => vi.unstubAllGlobals());

describe('call payload details', () => {
  beforeEach(() => {
    setActivePinia(createPinia());
    usePreferencesStore().prefs.locale = 'zh-CN';
    data.value = undefined;
    isPending.value = false;
    error.value = null;
    vi.clearAllMocks();
    useSnackbar().state.queue.splice(0);
  });

  it('distinguishes loading, historical absence and a retryable failure', async () => {
    isPending.value = true;
    const w = mount(CallPayloads, { props: { seq: 1 } });
    expect(w.find('[role="status"]').attributes('aria-label')).toContain('加载中');
    expect(w.findAll('.skeleton')).toHaveLength(3);
    isPending.value = false;
    data.value = { id: 'old', trace: null };
    await w.vm.$nextTick();
    expect(w.text()).toContain('历史记录未采集');
    error.value = new Error('404 Not Found');
    await w.vm.$nextTick();
    expect(w.text()).toContain('404 Not Found');
    expect(w.text()).not.toContain('历史记录未采集');
    await w.findComponent(AppButton).trigger('click');
    expect(refetch).toHaveBeenCalledOnce();
  });

  it('expands each payload independently and keeps incomplete badges visible while collapsed', async () => {
    data.value = { id: 'one', trace: {
      request: { model: 'model-a' }, response: { chunks: [], complete: false },
      client_request: { method: 'POST', body: '{"messages":[]}', complete: true },
      client_response: { status: 200, body: 'data: {"text":"你好"}\n\n', complete: false },
      upstream: [{ account: 'a1', error: 'ConnectError' }, { account: 'a2', response: { status: 200 } }],
    } };
    const w = mount(CallPayloads, { props: { seq: 1 } });
    const panels = w.findAllComponents(ExpansionPanel);
    expect(panels).toHaveLength(6);
    expect(w.find('details').exists()).toBe(false);
    expect(w.find('pre').exists()).toBe(false);
    expect(w.text()).toContain('客户端 HTTP 请求');
    expect(w.text()).toContain('客户端 HTTP 返回');
    expect(w.text()).toContain('上游请求与返回 2');
    expect(w.findAll('.badge.warning')).toHaveLength(2);
    await panels[4].find('button[aria-expanded]').trigger('click');
    expect(panels[4].find('button').attributes('aria-expanded')).toBe('true');
    expect(panels[4].find('pre').text()).toContain('a1');
    expect(panels[5].find('pre').exists()).toBe(false);
    await panels[5].find('button[aria-expanded]').trigger('click');
    expect(panels[5].find('pre').text()).toContain('a2');
    await panels[4].find('button[aria-expanded]').trigger('click');
    expect(panels[4].find('[role="region"]').exists()).toBe(false);
    expect(panels[5].find('[role="region"]').exists()).toBe(true);
    expect(w.text()).not.toContain('历史记录未采集');
  });

  it('renders SSE bodies as text, preserving newlines and never executing markup', async () => {
    const body = 'data: <script>alert(1)</script>\n\ndata: [DONE]\n\n';
    const writeText = vi.fn().mockResolvedValue(undefined);
    vi.stubGlobal('navigator', { clipboard: { writeText } });
    const w = mount(CallPayload, { props: { title: 'response', value: { status: 200, body } } });
    await w.find('button[aria-expanded]').trigger('click');
    expect(w.find('script').exists()).toBe(false);
    expect(w.find('pre').element.textContent).toContain(body);
    expect(w.find('pre').attributes('aria-label')).toBe('response');
    await w.findComponent(AppButton).trigger('click');
    await flushPromises();
    expect(writeText.mock.calls[0][0]).toContain(body);
    expect(useSnackbar().state.queue.at(-1)?.text).toContain('已复制');
  });

  it('reports clipboard failure without claiming success', async () => {
    vi.stubGlobal('navigator', { clipboard: { writeText: vi.fn().mockRejectedValue(new Error('denied')) } });
    const w = mount(CallPayload, { props: { title: 'request', value: { input: 'hello' } } });
    await w.find('button[aria-expanded]').trigger('click');
    await w.findComponent(AppButton).trigger('click');
    await flushPromises();
    const messages = useSnackbar().state.queue;
    expect(messages).toHaveLength(1);
    expect(messages[0].error).toBe(true);
    expect(messages[0].text).toContain('复制失败');
  });
});
