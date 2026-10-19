import { createPinia, setActivePinia } from 'pinia';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { streamChat } from '@/api/chat';
import { useAuthStore } from '@/stores/auth';

describe('streaming Chat Completions', () => {
  beforeEach(() => setActivePinia(createPinia()));
  afterEach(() => vi.unstubAllGlobals());

  it('delivers split UTF-8 and CRLF frames before the response finishes', async () => {
    let source!: ReadableStreamDefaultController<Uint8Array>;
    const stream = new ReadableStream<Uint8Array>({ start: (controller) => { source = controller; } });
    const fetch = vi.fn().mockResolvedValue(new Response(stream));
    vi.stubGlobal('fetch', fetch);
    useAuthStore().setToken('gateway-key');
    const delta = vi.fn();
    const request = streamChat('upstream/model', [], delta, undefined, { reasoning_effort: 'high' });
    const bytes = new TextEncoder().encode('data: {"choices":[{"index":0,"delta":{"content":"花"}}]}\r\n\r\n');
    const split = bytes.indexOf(0xe8) + 1;
    source.enqueue(bytes.slice(0, split));
    source.enqueue(bytes.slice(split, -1));
    await vi.waitFor(() => expect(fetch).toHaveBeenCalledOnce());
    expect(delta).not.toHaveBeenCalled();
    source.enqueue(bytes.slice(-1));
    await vi.waitFor(() => expect(delta).toHaveBeenCalledWith('花'));
    source.enqueue(new TextEncoder().encode('data: [DONE]\r\n\r\n'));
    await request;
    expect(stream.locked).toBe(false);
    const options = fetch.mock.calls[0][1];
    expect(fetch.mock.calls[0][0]).toContain('/api/v1/playground/chat/completions');
    expect(JSON.parse(options.body)).toMatchObject({ model: 'upstream/model', stream: true, reasoning_effort: 'high' });
    expect(options.headers.Authorization).toBe('Bearer gateway-key');
    useAuthStore().setToken(null);
  });

  it('reads a final delta even when EOF has no blank separator', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response('data: {"choices":[{"index":0,"delta":{"content":"last"}}]}')));
    const delta = vi.fn();
    await streamChat('model', [], delta);
    expect(delta).toHaveBeenCalledWith('last');
  });

  it('reports errors inside an HTTP 200 stream and releases the reader', async () => {
    const response = new Response('data: {"error":{"message":"Rate limit reached","code":"rate_limit"}}\n\n');
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(response));
    await expect(streamChat('model', [], vi.fn())).rejects.toMatchObject({ code: 'rate_limit', message: 'Rate limit reached' });
    expect(response.body!.locked).toBe(false);
  });

  it('requests authentication and displays the gateway error on HTTP 401', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response('{"message":"Tool key required","code":"auth_required"}', { status: 401 })));
    await expect(streamChat('model', [], vi.fn())).rejects.toMatchObject({ status: 401, message: 'Tool key required' });
    expect(useAuthStore().needed).toBe(true);
  });

  it('keeps tool authentication when the upstream provider rejects its own credential', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response('{"error":{"message":"Upstream key expired","code":"auth_failed"}}', { status: 401 })));
    await expect(streamChat('model', [], vi.fn())).rejects.toMatchObject({ status: 401, message: 'Upstream key expired' });
    expect(useAuthStore().needed).toBe(false);
  });
});
