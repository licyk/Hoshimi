import { flushPromises, mount } from '@vue/test-utils';
import { createPinia, setActivePinia } from 'pinia';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import AuthDialog from '@/components/AuthDialog.vue';
import { useAuthStore } from '@/stores/auth';

const state = vi.hoisted(() => ({ invalidate: vi.fn() }));
vi.mock('@tanstack/vue-query', () => ({ useQueryClient: () => ({ invalidateQueries: state.invalidate }) }));
vi.mock('@/ui', () => ({
  icons: {},
  AppDialog: { props: ['open', 'title'], emits: ['update:open'], template: '<div v-if="open"><h1>{{ title }}</h1><slot /><slot name="actions" /><button class="close" @click="$emit(\'update:open\', false)">Close</button></div>' },
  AppButton: { props: ['disabled', 'loading'], emits: ['click'], template: '<button class="submit" :disabled="disabled || loading" @click="$emit(\'click\')"><slot /></button>' },
  TextField: { props: ['modelValue', 'errorText', 'disabled'], emits: ['update:modelValue', 'enter'], template: '<div><input :disabled="disabled" :value="modelValue" @input="$emit(\'update:modelValue\', $event.target.value)" @keydown.enter="$emit(\'enter\')" /><p role="alert">{{ errorText }}</p></div>' },
}));

describe('automatic tool access login', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    localStorage.clear();
    setActivePinia(createPinia());
    useAuthStore().needed = true;
  });
  afterEach(() => vi.unstubAllGlobals());

  it('validates the tool key before saving and closes only after success', async () => {
    const fetch = vi.fn().mockResolvedValueOnce(new Response('{}', { status: 401 })).mockResolvedValueOnce(new Response('{}'));
    vi.stubGlobal('fetch', fetch);
    const wrapper = mount(AuthDialog);
    await wrapper.find('input').setValue('wrong');
    await wrapper.find('.submit').trigger('click');
    await flushPromises();
    expect(wrapper.find('[role="alert"]').text()).toContain('incorrect');
    expect(useAuthStore().needed).toBe(true);
    expect(useAuthStore().token).toBeNull();
    expect(state.invalidate).not.toHaveBeenCalled();
    await wrapper.find('input').setValue(' tool-key ');
    await wrapper.find('.submit').trigger('click');
    await flushPromises();
    expect(fetch.mock.calls[1][0]).toContain('/api/v1/app/meta');
    expect(fetch.mock.calls[1][1].headers.Authorization).toBe('Bearer tool-key');
    expect(useAuthStore().token).toBe('tool-key');
    expect(localStorage.getItem('hoshimi:access-token')).toBe('tool-key');
    expect(useAuthStore().needed).toBe(false);
    expect(state.invalidate).toHaveBeenCalledOnce();
    wrapper.unmount();
  });

  it('keeps the saved key and dialog when verification cannot reach the server', async () => {
    useAuthStore().setToken('previous');
    useAuthStore().needed = true;
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new Error('offline')));
    const wrapper = mount(AuthDialog);
    await wrapper.find('input').setValue('candidate');
    await wrapper.find('.submit').trigger('click');
    await flushPromises();
    expect(wrapper.find('[role="alert"]').text()).toContain('Unable to verify');
    expect(useAuthStore().token).toBe('previous');
    expect(useAuthStore().needed).toBe(true);
    wrapper.unmount();
  });

  it('aborts verification on close and ignores a late response', async () => {
    let resolve!: (response: Response) => void;
    const fetch = vi.fn().mockImplementation(() => new Promise<Response>((done) => { resolve = done; }));
    vi.stubGlobal('fetch', fetch);
    const wrapper = mount(AuthDialog);
    await wrapper.find('input').setValue('candidate');
    await wrapper.find('input').trigger('keydown', { key: 'Enter' });
    await wrapper.find('input').trigger('keydown', { key: 'Enter' });
    expect(fetch).toHaveBeenCalledOnce();
    await wrapper.find('.close').trigger('click');
    expect(fetch.mock.calls[0][1].signal.aborted).toBe(true);
    resolve(new Response('{}'));
    await flushPromises();
    expect(useAuthStore().token).toBeNull();
    expect(state.invalidate).not.toHaveBeenCalled();
    wrapper.unmount();
  });
});
