import { flushPromises, mount } from '@vue/test-utils';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { h } from 'vue';
import ErrorNotice from '@/ui/ErrorNotice.vue';
import Snackbar from '@/ui/Snackbar.vue';
import { useSnackbar } from '@/ui/useSnackbar';

describe('ErrorNotice', () => {
  it('renders error text literally, including line breaks and markup', () => {
    const message = 'Request failed\n<img src=x onerror=alert(1)>';
    const w = mount(ErrorNotice, { props: { title: 'API error', message, hint: 'Check the API address.' } });
    expect(w.find('.message').text()).toBe(message);
    expect(w.find('img').exists()).toBe(false);
    expect(w.text()).toContain('Check the API address.');
    expect(w.find('svg').attributes('aria-hidden')).toBe('true');
    expect(w.attributes('role')).toBe('status');
  });

  it('announces submitted errors and keeps historical diagnostics quiet', async () => {
    const w = mount(ErrorNotice, { props: { message: 'Failed', announcement: 'assertive' } });
    expect(w.attributes('role')).toBe('alert');
    expect(w.attributes('aria-live')).toBe('assertive');
    await w.setProps({ announcement: 'off' });
    expect(w.attributes('role')).toBeUndefined();
    expect(w.attributes('aria-live')).toBeUndefined();
  });

  it('keeps caller actions usable alongside structured output and metadata', async () => {
    const retry = vi.fn();
    const w = mount(ErrorNotice, {
      props: { compact: true },
      slots: { default: '<pre>tool output\nsecond line</pre>', meta: 'HTTP 503', actions: () => h('button', { onClick: retry }, 'Retry') },
    });
    expect(w.find('pre').text()).toContain('second line');
    expect(w.find('.meta').text()).toBe('HTTP 503');
    expect(w.find('.hint').exists()).toBe(false);
    await w.find('button').trigger('click');
    expect(retry).toHaveBeenCalledOnce();
  });
});

describe('error snackbars', () => {
  afterEach(() => {
    useSnackbar().state.queue.splice(0);
    vi.useRealTimers();
  });

  it('uses the same error presentation and preserves actions, dismissal and the queue', async () => {
    vi.useFakeTimers();
    const queue = useSnackbar();
    queue.state.queue.splice(0);
    const action = vi.fn();
    queue.show('Could not save', { error: true, actionLabel: 'Retry', action });
    queue.show('Saved');
    const w = mount(Snackbar, { global: { stubs: { teleport: true, transition: true, IconButton: { props: ['label'], template: '<button :aria-label="label" />' } } } });
    try {
      expect(w.findComponent(ErrorNotice).props('message')).toBe('Could not save');
      await w.find('button.action').trigger('click');
      expect(action).toHaveBeenCalledOnce();
      expect(w.findComponent(ErrorNotice).exists()).toBe(false);
      expect(w.find('.snackbar').text()).toContain('Saved');
      await w.find('button.close').trigger('click');
      expect(queue.state.queue).toHaveLength(0);
      queue.error('Network unavailable');
      await flushPromises();
      expect(w.findComponent(ErrorNotice).exists()).toBe(true);
      await vi.advanceTimersByTimeAsync(7000);
      expect(queue.state.queue).toHaveLength(0);
    } finally {
      w.unmount();
    }
  });
});
