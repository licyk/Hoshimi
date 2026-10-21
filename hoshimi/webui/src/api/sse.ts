/**
 * Server-sent events over ``fetch``. ``EventSource`` cannot send the access key as a header, and
 * the management API takes it only that way, so the stream is read by hand: frames are split on
 * blank lines and each frame's ``data:`` lines are joined and parsed as JSON. Frames without data
 * (comments) and pings are skipped here, so a reader only sees events.
 */
import { BASE_URL } from '@/api/baseUrl';
import { ApiError, handleToolAuthentication } from '@/api/client';
import { useAuthStore } from '@/stores/auth';

export interface StreamOptions {
  signal?: AbortSignal;
  /** Called with each parsed event, in order. Returning ``true`` ends the read early. */
  onEvent: (event: unknown) => boolean | void;
  /** Called once the server has answered with an open stream, before any event. */
  onOpen?: () => void;
}

/** The JSON of one frame's ``data:`` lines, or undefined for a frame that carries none. */
export function parseFrame(frame: string): unknown {
  const payload = frame
    .split(/\r?\n/)
    .filter((line) => line.startsWith('data:'))
    .map((line) => line.slice(5).trimStart())
    .join('\n');
  if (!payload) return undefined;
  const event = JSON.parse(payload) as unknown;
  if (event && typeof event === 'object' && (event as { type?: unknown }).type === 'ping') return undefined;
  return event;
}

/**
 * Read ``path`` as an event stream until it ends, ``onEvent`` asks to stop, or ``signal`` aborts.
 * Resolves ``true`` when the server ended the stream (a reader may reconnect), ``false`` when the
 * reader stopped it; a refused request throws an ``ApiError``.
 */
export async function readEventStream(path: string, options: StreamOptions): Promise<boolean> {
  const token = useAuthStore().token;
  const authorization = token ? `Bearer ${token}` : null;
  const response = await fetch(`${BASE_URL}${path}`, {
    headers: { Accept: 'text/event-stream', ...(authorization ? { Authorization: authorization } : {}) },
    signal: options.signal,
  });
  if (!response.ok) {
    await handleToolAuthentication(response, authorization);
    throw new ApiError(response.status, 'stream_failed', `${response.status} ${response.statusText}`);
  }
  if (!response.body) throw new ApiError(0, 'stream_failed', 'The server sent no event stream');
  options.onOpen?.();
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  const consume = (frame: string): boolean => {
    if (options.signal?.aborted) throw new DOMException('Aborted', 'AbortError');
    const event = parseFrame(frame);
    return event === undefined ? false : options.onEvent(event) === true;
  };
  try {
    for (;;) {
      const { value, done } = await reader.read();
      buffer += decoder.decode(value, { stream: !done });
      const frames = buffer.split(/\r?\n\r?\n/);
      buffer = frames.pop() ?? '';
      for (const frame of frames) if (consume(frame)) return false;
      if (done) {
        if (buffer.trim() && consume(buffer)) return false;
        return true;
      }
    }
  } finally {
    try {
      await reader.cancel();
    } finally {
      reader.releaseLock();
    }
  }
}
