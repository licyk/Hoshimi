/** Chat Completions from the browser: one non-streaming call, and one streaming call. */
import { BASE_URL } from '@/api/baseUrl';
import { ApiError, handleToolAuthentication, postJson } from '@/api/client';
import { useAuthStore } from '@/stores/auth';

export interface ChatMessage {
  role: 'system' | 'user' | 'assistant';
  content: string;
}

export interface Usage {
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
}

export interface ChatCompletion {
  id: string;
  model: string;
  choices: { index: number; message: { role: string; content: string | null }; finish_reason: string | null }[];
  usage?: Usage | null;
}

interface ChatChunk {
  error?: { message?: string; type?: string; code?: string };
  choices?: { index: number; delta?: { role?: string; content?: string | null }; finish_reason?: string | null }[];
  usage?: Usage | null;
}

/** The non-streaming call: the whole message comes back at once. */
export const createChat = (model: string, messages: ChatMessage[]): Promise<ChatCompletion> =>
  postJson<ChatCompletion>('/api/v1/playground/chat/completions', { model, messages, stream: false });

async function openAiError(response: Response): Promise<ApiError> {
  let body: unknown = null;
  try {
    body = await response.json();
  } catch {
    /* not JSON */
  }
  const error = (body as { error?: { message?: string; type?: string; code?: string } } | null)?.error;
  if (error?.message) return new ApiError(response.status, error.code ?? error.type ?? 'error', error.message);
  const direct = body as { code?: string; message?: string } | null;
  if (direct?.message) return new ApiError(response.status, direct.code ?? 'error', direct.message);
  return new ApiError(response.status, 'error', `${response.status} ${response.statusText}`);
}

/**
 * Extra body fields the caller chose to send, e.g. `reasoning_effort`. Left out entirely when
 * the picker is on "auto": the gateway treats an absent level as "keep what I was given".
 */
export type ChatOptions = Record<string, unknown>;

/**
 * A POST with the browser's access key, as JSON (or the ``body`` given as is), that throws an
 * ApiError for any failure, in OpenAI's error shape or the management API's.
 */
export async function authedFetch(path: string, init: RequestInit & { json?: unknown } = {}): Promise<Response> {
  const token = useAuthStore().token;
  const { json, headers, ...rest } = init;
  const response = await fetch(`${BASE_URL}${path}`, {
    ...rest,
    headers: { ...(json !== undefined ? { 'Content-Type': 'application/json' } : {}), ...(token ? { Authorization: `Bearer ${token}` } : {}), ...headers },
    body: json !== undefined ? JSON.stringify(json) : rest.body,
  });
  if (!response.ok) {
    await handleToolAuthentication(response, token ? `Bearer ${token}` : null);
    throw await openAiError(response);
  }
  return response;
}

/**
 * Read a Server-Sent Events response, handing each event's data to ``consume`` as it arrives; stops
 * when ``consume`` returns true or the stream ends. Split UTF-8 and CRLF frames are put back together.
 */
export async function readSse(response: Response, consume: (payload: string) => boolean | void): Promise<void> {
  if (!response.body) throw new ApiError(0, 'network', 'The server sent no stream');
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  const frame = (text: string) => {
    const payload = text.split(/\r?\n/).filter((line) => line.startsWith('data:')).map((line) => line.slice(5).trimStart()).join('\n').trim();
    return payload ? consume(payload) === true : false;
  };
  try {
    for (;;) {
      const { done, value } = await reader.read();
      buffer += decoder.decode(value, { stream: !done });
      const frames = buffer.split(/\r?\n\r?\n/);
      buffer = frames.pop() ?? '';
      for (const f of frames) {
        if (frame(f)) return;
      }
      if (done) {
        if (buffer.trim()) frame(buffer);
        return;
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

/** Deliver SSE deltas as they arrive, including errors sent after the response headers. */
export async function streamChat(
  model: string,
  messages: ChatMessage[],
  onDelta: (text: string) => void,
  signal?: AbortSignal,
  options?: ChatOptions,
): Promise<void> {
  const response = await authedFetch('/api/v1/playground/chat/completions', {
    method: 'POST',
    headers: { Accept: 'text/event-stream' },
    json: { ...options, model, messages, stream: true },
    signal,
  });
  await readSse(response, (payload) => {
    if (payload === '[DONE]') return true;
    const chunk = JSON.parse(payload) as ChatChunk;
    if (chunk.error) {
      throw new ApiError(response.status, chunk.error.code ?? chunk.error.type ?? 'upstream_error', chunk.error.message ?? 'The upstream stream failed');
    }
    const text = chunk.choices?.[0]?.delta?.content;
    if (text) onDelta(text);
  });
}
