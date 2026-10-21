import { keepPreviousData, useInfiniteQuery, useMutation, useQuery, useQueryClient } from '@tanstack/vue-query';
import { computed, type Ref } from 'vue';
import { api, unwrap } from '@/api/client';
import { keys } from '@/api/queries/keys';
import type { CallPage } from '@/api/types';

export type UsageRange = '24h' | '7d' | '30d' | '90d';
export const RANGES: readonly UsageRange[] = ['24h', '7d', '30d', '90d'];

/**
 * What the gateway served over a range: totals against the period before, the series for the
 * chart and the breakdowns. A new range keeps the old answer on screen (dimmed by the page) until
 * the new one lands, so the frame never collapses; the live store invalidates it as calls finish.
 */
export const useUsageReport = (range: Ref<UsageRange>) =>
  useQuery({
    queryKey: () => keys.usage(range.value),
    queryFn: () => unwrap(api.GET('/api/v1/stats', { params: { query: { range: range.value } } })),
    placeholderData: keepPreviousData,
  });

export interface CallFilters {
  model?: string;
  provider?: string;
  account?: string;
  origin?: string;
  ok?: boolean;
}

/** The call history, newest first, a page at a time as the list scrolls. */
export const useCalls = (filters: Ref<CallFilters>) =>
  useInfiniteQuery({
    queryKey: computed(() => keys.calls({ ...filters.value })),
    queryFn: ({ pageParam }): Promise<CallPage> =>
      unwrap(api.GET('/api/v1/stats/calls', { params: { query: { limit: 50, ...filters.value, before: pageParam } } })),
    initialPageParam: null as number | null,
    getNextPageParam: (page: CallPage) => page.next ?? undefined,
    placeholderData: keepPreviousData,
  });

/** Forget every counter and call: the report starts again from nothing. */
export function useResetStats() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => unwrap(api.DELETE('/api/v1/stats')),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: keys.stats });
      qc.invalidateQueries({ queryKey: keys.channels });
    },
  });
}
