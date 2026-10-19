import { flushPromises, mount } from '@vue/test-utils';
import { QueryClient, VueQueryPlugin } from '@tanstack/vue-query';
import { createPinia } from 'pinia';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { defineComponent, h } from 'vue';
import { useUpdateSettings } from '@/api/queries/app';
import { useAuthStore } from '@/stores/auth';

const state = vi.hoisted(() => {
  const request = vi.fn();
  vi.stubGlobal('fetch', request);
  return { request };
});

describe('saving tool access keys', () => {
  afterEach(() => vi.unstubAllGlobals());

  it.each([['new-tool', 'new-tool'], [null, null]])('updates this browser only after the server accepts %s', async (candidate, expected) => {
    state.request.mockReset().mockResolvedValueOnce(new Response('{"code":"invalid_input","message":"Rejected"}', { status: 400 }))
      .mockResolvedValueOnce(new Response('{"server":{"access_token_configured":true}}', { headers: { 'Content-Type': 'application/json' } }));
    const view = defineComponent({
      setup() {
        const auth = useAuthStore();
        auth.setToken('previous');
        const mutation = useUpdateSettings();
        return () => h('button', { onClick: () => mutation.mutate({ server: { access_token: candidate } }) }, 'Save');
      },
    });
    const qc = new QueryClient({ defaultOptions: { mutations: { retry: false } } });
    const wrapper = mount(view, { global: { plugins: [createPinia(), [VueQueryPlugin, { queryClient: qc }]] } });
    await wrapper.find('button').trigger('click');
    await flushPromises();
    expect(useAuthStore().token).toBe('previous');
    await wrapper.find('button').trigger('click');
    await flushPromises();
    expect(useAuthStore().token).toBe(expected);
    wrapper.unmount();
    qc.clear();
  });
});
