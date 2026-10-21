/** Provider-sized catalogue updates shared by every model picker. */
import { useQuery, useQueryClient } from '@tanstack/vue-query';
import { ApiError } from '@/api/client';
import { keys } from '@/api/queries/keys';
import { readEventStream } from '@/api/sse';
import type { S } from '@/api/types';

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
  let snapshot: CatalogSnapshot | undefined;
  try {
    await readEventStream('/api/v1/catalog/stream', {
      signal,
      onEvent: (raw) => {
        const event = raw as S['CatalogEvent'];
        if (event.type === 'snapshot') {
          snapshot = { levels: event.levels, providers: event.providers, done: false };
        } else if (event.type === 'provider' && snapshot && event.provider) {
          const update = event.provider;
          snapshot = { ...snapshot, providers: snapshot.providers.map((p) => (p.id === update.id ? update : p)) };
        } else if (event.type === 'done' && snapshot && !snapshot.providers.some((p) => p.status === 'loading')) {
          snapshot = { ...snapshot, done: true };
        } else {
          throw new ApiError(0, 'catalog_failed', 'Invalid catalogue stream');
        }
        onUpdate(snapshot);
        return snapshot.done;
      },
    });
  } catch (error) {
    if (error instanceof ApiError && error.code === 'stream_failed') throw new ApiError(error.status, 'catalog_failed', error.message);
    throw error;
  }
  if (snapshot?.done) return snapshot;
  throw new ApiError(0, 'catalog_interrupted', 'The catalogue stream ended before all providers reported');
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
