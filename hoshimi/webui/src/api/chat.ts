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

/** Deliver SSE deltas as they arrive, including errors sent after the response headers. */
export async function streamChat(
  model: string,
  messages: ChatMessage[],
  onDelta: (text: string) => void,
  signal?: AbortSignal,
  options?: ChatOptions,
): Promise<void> {
  const token = useAuthStore().token;
  const response = await fetch(`${BASE_URL}/api/v1/playground/chat/completions`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Accept: 'text/event-stream', ...(token ? { Authorization: `Bearer ${token}` } : {}) },
    body: JSON.stringify({ ...options, model, messages, stream: true }),
    signal,
  });
  if (!response.ok) {
    await handleToolAuthentication(response, token ? `Bearer ${token}` : null);
    throw await openAiError(response);
  }
  if (!response.body) throw new ApiError(0, 'network', 'The server sent no stream');

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';

  function consume(frame: string): boolean {
    const payload = frame.split(/\r?\n/).filter((line) => line.startsWith('data:')).map((line) => line.slice(5).trimStart()).join('\n').trim();
    if (!payload) return false;
    if (payload === '[DONE]') return true;
    const chunk = JSON.parse(payload) as ChatChunk;
    if (chunk.error) {
      throw new ApiError(response.status, chunk.error.code ?? chunk.error.type ?? 'upstream_error', chunk.error.message ?? 'The upstream stream failed');
    }
    const text = chunk.choices?.[0]?.delta?.content;
    if (text) onDelta(text);
    return false;
  }

  try {
    for (;;) {
      const { done, value } = await reader.read();
      buffer += decoder.decode(value, { stream: !done });
      const frames = buffer.split(/\r?\n\r?\n/);
      buffer = frames.pop() ?? '';
      for (const frame of frames) {
        if (consume(frame)) return;
      }
      if (done) {
        if (buffer.trim()) consume(buffer);
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
