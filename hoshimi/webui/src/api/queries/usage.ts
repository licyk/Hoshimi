import { keepPreviousData, useInfiniteQuery, useMutation, useQuery, useQueryClient, type QueryClient } from '@tanstack/vue-query';
import { computed, type Ref } from 'vue';
import { api, unwrap } from '@/api/client';
import { keys } from '@/api/queries/keys';
import type { CallPage } from '@/api/types';

export type UsageRange = 'day' | '24h' | '7d' | '30d' | '90d';
export const RANGES: readonly UsageRange[] = ['day', '24h', '7d', '30d', '90d'];

/** A report's slice: a range, and for ``day`` the calendar day (none is today). */
export interface UsageSlice {
  range: UsageRange;
  date?: string | null;
}

const reportQuery = (slice: UsageSlice) => ({
  queryKey: keys.usage(slice.range, slice.range === 'day' ? (slice.date ?? null) : null),
  queryFn: () => unwrap(api.GET('/api/v1/stats', { params: { query: { range: slice.range, ...(slice.range === 'day' && slice.date ? { date: slice.date } : {}) } } })),
});

/**
 * What the gateway served over a slice: totals against the period before, the series for the
 * chart and the breakdowns. A new slice keeps the old answer on screen until the new one lands, so
 * the frame never collapses and the page moves to the new numbers in one step; the live store
 * invalidates it as calls finish.
 */
export const useUsageReport = (slice: Ref<UsageSlice>) =>
  useQuery({
    queryKey: computed(() => reportQuery(slice.value).queryKey),
    queryFn: () => reportQuery(slice.value).queryFn(),
    placeholderData: keepPreviousData,
  });

/** Every range's report for today, fetched quietly once the page has its own: switching is then instant. */
export function prefetchUsage(qc: QueryClient) {
  for (const range of RANGES) void qc.prefetchQuery(reportQuery({ range }));
}

/** The days that hold any usage, with their totals: the calendar shades them. */
export const useUsageDays = () =>
  useQuery({
    queryKey: keys.usageDays,
    queryFn: () => unwrap(api.GET('/api/v1/stats/days')),
  });

export interface CallFilters {
  model?: string;
  provider?: string;
  account?: string;
  origin?: string;
  ok?: boolean;
  /** ISO datetimes: from (inclusive) and until (exclusive). */
  since?: string;
  until?: string;
  /** Matches the model, the route, the finish reason or the error. */
  q?: string;
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
