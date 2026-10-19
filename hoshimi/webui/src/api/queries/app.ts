import { useMutation, useQuery, useQueryClient } from '@tanstack/vue-query';
import { api, unwrap } from '@/api/client';
import { catalogSpecs, useCatalog } from '@/api/catalog';
import { keys } from '@/api/queries/keys';
import type { ModelList, SettingsView } from '@/api/types';
import { useAuthStore } from '@/stores/auth';

export const useMeta = () => useQuery({ queryKey: keys.meta, queryFn: () => unwrap(api.GET('/api/v1/app/meta')), staleTime: Infinity });

export const useVersion = () => useQuery({ queryKey: keys.version, queryFn: () => unwrap(api.GET('/api/v1/app/version')), staleTime: Infinity });

export const useHealth = () => useQuery({ queryKey: keys.health, queryFn: () => unwrap(api.GET('/api/v1/app/health')) });

export const useSettings = () => useQuery({ queryKey: keys.settings, queryFn: () => unwrap(api.GET('/api/v1/settings')) });

export const useProviders = () => useQuery({ queryKey: keys.providers, queryFn: () => unwrap(api.GET('/api/v1/providers')) });

/** The capability catalogue: every id with its effort menu and its last availability verdict. */
export const useModelSpecs = () => useCatalog(catalogSpecs);

/** What this gateway served: per-day and per-model totals, plus the recent calls. */
export const useStats = () => useQuery({ queryKey: keys.stats, queryFn: () => unwrap(api.GET('/api/v1/stats')) });

/** The last probe round: verdicts here, and the models row re-read when a round ends. */
export const useProbe = () =>
  useQuery({
    queryKey: keys.probe,
    queryFn: () => unwrap(api.GET('/api/v1/probe')),
    // While a round runs the answer is only stale by a second: poll it, then stop.
    refetchInterval: (query) => (query.state.data?.running ? 1500 : false),
  });

export function useStartProbe() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => unwrap(api.POST('/api/v1/probe', { body: {} })),
    onSuccess: (data) => {
      qc.setQueryData(keys.probe, data);
      if (!data.running) qc.invalidateQueries({ queryKey: keys.catalog });
    },
  });
}

export const useModels = () => useCatalog<ModelList>((snapshot) => ({ object: 'list', data: catalogSpecs(snapshot).data.map((s) => ({ id: s.id, object: 'model', created: 0, owned_by: s.provider, free: s.free })) }));

export function useUpdateSettings() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (patch: Record<string, unknown>) => unwrap(api.PATCH('/api/v1/settings', { body: patch })),
    onSuccess: (data: SettingsView, patch: Record<string, unknown>) => {
      const server = patch.server as Record<string, unknown> | undefined;
      if (server && Object.hasOwn(server, 'access_token')) {
        useAuthStore().setToken(typeof server.access_token === 'string' ? server.access_token.trim() || null : null);
      }
      qc.setQueryData(keys.settings, data);
      // Providers, the default model and the key are all shown elsewhere too.
      qc.invalidateQueries({ queryKey: keys.providers });
      qc.invalidateQueries({ queryKey: keys.meta });
      qc.invalidateQueries({ queryKey: keys.catalog });
    },
  });
}

export type { ModelList };
