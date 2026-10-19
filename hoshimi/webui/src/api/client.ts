import createClient, { type Middleware } from 'openapi-fetch';
import { BASE_URL } from '@/api/baseUrl';
import type { paths } from '@/api/schema';
import { useAuthStore } from '@/stores/auth';

export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
    message: string,
    readonly detail: Record<string, unknown> = {},
  ) {
    super(message);
  }
}

const auth: Middleware = {
  onRequest({ request }) {
    const token = useAuthStore().token;
    if (token) request.headers.set('Authorization', `Bearer ${token}`);
    return request;
  },
  async onResponse({ request, response }) {
    await handleToolAuthentication(response, request.headers.get('Authorization'));
    return response;
  },
};

/** Ask for the tool key only for this browser's current credential and a gateway auth refusal. */
export async function handleToolAuthentication(response: Response, authorization: string | null): Promise<void> {
  if (response.status !== 401) return;
  const auth = useAuthStore();
  if (authorization !== (auth.token ? `Bearer ${auth.token}` : null)) return;
  try {
    const body = await response.clone().json();
    if (body?.code === 'auth_required' && authorization === (auth.token ? `Bearer ${auth.token}` : null)) auth.needed = true;
  } catch {
    /* Provider failures and non-JSON responses do not invalidate tool authentication. */
  }
}

export const api = createClient<paths>({ baseUrl: BASE_URL });
api.use(auth);

type Result<T> = { data?: T; error?: unknown; response: Response };

function toError(error: unknown, response: Response): ApiError {
  if (error && typeof error === 'object') {
    const e = error as { code?: string; message?: string; detail?: unknown };
    if (typeof e.message === 'string') return new ApiError(response.status, e.code ?? 'error', e.message, (e.detail as Record<string, unknown>) ?? {});
    if (Array.isArray(e.detail)) {
      const first = e.detail[0] as { msg?: string; loc?: unknown[] } | undefined;
      return new ApiError(response.status, 'invalid_input', first?.msg ?? 'Invalid input', { errors: e.detail });
    }
  }
  return new ApiError(response.status, 'error', `${response.status} ${response.statusText}`);
}

/** Await an openapi-fetch call and return its data, or throw an ApiError with the server's message. */
export async function unwrap<T>(call: Promise<Result<T>>): Promise<T> {
  const { data, error, response } = await call;
  if (!response.ok) throw toError(error, response);
  return data as T;
}

/** A ``/v1/*`` failure arrives in OpenAI's shape: ``{error: {message, type, code}}``. */
function toOpenAiError(body: unknown, response: Response): ApiError {
  const wrapped = (body as { error?: { message?: string; type?: string; code?: string } } | null)?.error;
  if (wrapped?.message) return new ApiError(response.status, wrapped.code ?? wrapped.type ?? 'error', wrapped.message);
  return toError(body, response);
}

/** POST a JSON body and return the parsed response, throwing an ApiError for any failure. */
export async function postJson<T>(path: string, body: unknown, signal?: AbortSignal): Promise<T> {
  const token = useAuthStore().token;
  const response = await fetch(`${BASE_URL}${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...(token ? { Authorization: `Bearer ${token}` } : {}) },
    body: JSON.stringify(body),
    signal,
  });
  if (!response.ok) {
    await handleToolAuthentication(response, token ? `Bearer ${token}` : null);
    let parsed: unknown = null;
    try {
      parsed = await response.json();
    } catch {
      /* not JSON */
    }
    throw toOpenAiError(parsed, response);
  }
  return (await response.json()) as T;
}
