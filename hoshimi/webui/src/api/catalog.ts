/** Provider-sized catalogue updates shared by every model picker. */
import { useQuery, useQueryClient } from '@tanstack/vue-query';
import { BASE_URL } from '@/api/baseUrl';
import { ApiError, handleToolAuthentication } from '@/api/client';
import { keys } from '@/api/queries/keys';
import type { S } from '@/api/types';
import { useAuthStore } from '@/stores/auth';

export interface CatalogSnapshot {
  levels: S['EffortOption'][];
  providers: S['CatalogProvider'][];
  done: boolean;
}

export function catalogSpecs(snapshot: CatalogSnapshot): S['ModelSpecList'] {
  const seen = new Set<string>();
  return {
    levels: snapshot.levels,
    data: snapshot.providers.flatMap((p) => p.data).filter((model) => {
      if (seen.has(model.id)) return false;
      seen.add(model.id);
      return true;
    }),
  };
}

export async function readCatalog(onUpdate: (snapshot: CatalogSnapshot) => void, signal?: AbortSignal): Promise<CatalogSnapshot> {
  const token = useAuthStore().token;
  const authorization = token ? `Bearer ${token}` : null;
  const response = await fetch(`${BASE_URL}/api/v1/catalog/stream`, {
    headers: { Accept: 'text/event-stream', ...(authorization ? { Authorization: authorization } : {}) }, signal,
  });
  if (!response.ok) {
    await handleToolAuthentication(response, authorization);
    throw new ApiError(response.status, 'catalog_failed', `${response.status} ${response.statusText}`);
  }
  if (!response.body) throw new ApiError(0, 'catalog_failed', 'The server sent no catalogue stream');
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  let snapshot: CatalogSnapshot | undefined;
  function consume(frame: string) {
    if (signal?.aborted) throw new DOMException('Aborted', 'AbortError');
    const payload = frame.split(/\r?\n/).filter((line) => line.startsWith('data:')).map((line) => line.slice(5).trimStart()).join('\n');
    if (!payload) return;
    const event = JSON.parse(payload) as S['CatalogEvent'];
    if (event.type === 'snapshot') {
      snapshot = { levels: event.levels, providers: event.providers, done: false };
    } else if (event.type === 'provider' && snapshot && event.provider) {
      const update = event.provider;
      snapshot = { ...snapshot, providers: snapshot.providers.map((p) => p.id === update.id ? update : p) };
    } else if (event.type === 'done' && snapshot && !snapshot.providers.some((p) => p.status === 'loading')) {
      snapshot = { ...snapshot, done: true };
    } else {
      throw new ApiError(0, 'catalog_failed', 'Invalid catalogue stream');
    }
    onUpdate(snapshot);
  }
  try {
    for (;;) {
      const { value, done } = await reader.read();
      buffer += decoder.decode(value, { stream: !done });
      const frames = buffer.split(/\r?\n\r?\n/);
      buffer = frames.pop() ?? '';
      for (const frame of frames) consume(frame);
      if (snapshot?.done) return snapshot;
      if (done) {
        if (buffer.trim()) consume(buffer);
        if (snapshot?.done) return snapshot;
        throw new ApiError(0, 'catalog_interrupted', 'The catalogue stream ended before all providers reported');
      }
    }
  } finally {
    try { await reader.cancel(); } finally { reader.releaseLock(); }
  }
}

export function useCatalog<T = CatalogSnapshot>(select?: (data: CatalogSnapshot) => T) {
  const qc = useQueryClient();
  return useQuery({
    queryKey: keys.catalog,
    queryFn: ({ signal }) => readCatalog((snapshot) => qc.setQueryData(keys.catalog, snapshot), signal),
    select,
    staleTime: 30_000,
    // Retrying a broken transport automatically can hide a partial catalogue.
    // Keep it visible and let the operator explicitly retry.
    retry: false,
  });
}
