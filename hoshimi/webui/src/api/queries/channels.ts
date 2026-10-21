import { useMutation, useQuery, useQueryClient } from '@tanstack/vue-query';
import { api, unwrap } from '@/api/client';
import { keys } from '@/api/queries/keys';
import type { components } from '@/api/schema';

type BalanceReport = components['schemas']['BalanceReport'];
type ClaimReport = components['schemas']['ClaimReport'];
type RefreshReport = components['schemas']['RefreshReport'];
type LoginSession = components['schemas']['LoginSessionView'];
export type PassiveQuota = components['schemas']['PassiveQuota'];

/**
 * Every channel with its accounts, their renewal state, allowance, network and today's usage, in
 * one answer the server builds without asking any station. Balances need a round trip to the
 * stations, so they stay a query of their own (``useBalances``), joined in by account.
 */
export const useChannels = () =>
  useQuery({
    queryKey: keys.channels,
    queryFn: () => unwrap(api.GET('/api/v1/channels')),
    // A renewal round that the timer is running shows up as it settles.
    refetchInterval: (query) => (query.state.data?.auto_refresh ? 30_000 : false),
  });

/** Remaining balance (or quota window) per account; a lane without an endpoint is skipped. */
export const useBalances = () => useQuery({ queryKey: keys.balances, queryFn: () => unwrap(api.GET('/api/v1/credits')) });

/** Per-account token state: which lane has run out of renewal and needs a human. */
export const useRefreshStatus = () =>
  useQuery({
    queryKey: keys.refresh,
    queryFn: () => unwrap(api.GET('/api/v1/refresh')),
    // A renewal round that the timer is running shows up as it settles.
    refetchInterval: (query) => (query.state.data?.auto_refresh ? 30_000 : false),
  });

/** Stations that offer an interactive login through this gateway. */
export const useLoginProviders = () => useQuery({ queryKey: keys.loginProviders, queryFn: () => unwrap(api.GET('/api/v1/login/providers')), staleTime: Infinity });

/** The stations an `agg` account may name instead of a URL. A published table, so never refetched. */
export const useStations = () => useQuery({ queryKey: keys.stations, queryFn: () => unwrap(api.GET('/api/v1/stations')), staleTime: Infinity });
export const useProviderForms = () => useQuery({ queryKey: keys.providerForms, queryFn: () => unwrap(api.GET('/api/v1/provider-forms')), staleTime: Infinity });

export function useClaimCredits() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (provider: string | null) => unwrap(api.POST('/api/v1/credits/claim', { body: provider ? { provider } : {} })),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: keys.balances });
      qc.invalidateQueries({ queryKey: keys.channels });
    },
  });
}

export function useRefreshNow() {
  const qc = useQueryClient();
  return useMutation({
    // `force` is part of the request shape; an on-demand round keeps the same window the timer uses.
    mutationFn: (provider: string | null) => unwrap(api.POST('/api/v1/refresh', { body: { force: false, ...(provider ? { provider } : {}) } })),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: keys.refresh });
      qc.invalidateQueries({ queryKey: keys.providers });
      qc.invalidateQueries({ queryKey: keys.channels });
    },
  });
}

export function useDeleteAccount() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ provider, account }: { provider: string; account: string }) =>
      unwrap(api.DELETE('/api/v1/providers/{provider_id}/accounts/{account_id}', { params: { path: { provider_id: provider, account_id: account } } })),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: keys.providers });
      qc.invalidateQueries({ queryKey: keys.channels });
      qc.invalidateQueries({ queryKey: keys.refresh });
      qc.invalidateQueries({ queryKey: keys.balances });
      qc.invalidateQueries({ queryKey: keys.catalog });
    },
  });
}

/** Append one credential to a lane: the config file and the running pool move together. */
export function useAddAccount() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ provider, account }: { provider: string; account: Record<string, unknown> }) =>
      unwrap(api.POST('/api/v1/providers/{provider_id}/accounts', { params: { path: { provider_id: provider } }, body: { account } })),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: keys.providers });
      qc.invalidateQueries({ queryKey: keys.channels });
      qc.invalidateQueries({ queryKey: keys.settings });
      qc.invalidateQueries({ queryKey: keys.catalog });
    },
  });
}

/** Begin a login: the session carries the station's browser URL as soon as it issues one. */
export const startLogin = (provider: string) => unwrap(api.POST('/api/v1/login/start', { body: { provider } }));

/** One login session's current state; the caller polls it while ``status`` is pending. */
export const pollLogin = (sessionId: string) => unwrap(api.GET('/api/v1/login/poll', { params: { query: { session_id: sessionId } } }));
export const submitLoginInput = (sessionId: string, value: string) => unwrap(api.POST('/api/v1/login/input', { body: { session_id: sessionId, value } }));
export const cancelLogin = (sessionId: string) => unwrap(api.POST('/api/v1/login/cancel', { params: { query: { session_id: sessionId } } }));

/** After a login lands, the account list and its state have all moved. */
export function useLoginDone() {
  const qc = useQueryClient();
  return () => {
    qc.invalidateQueries({ queryKey: keys.providers });
      qc.invalidateQueries({ queryKey: keys.channels });
    qc.invalidateQueries({ queryKey: keys.refresh });
    qc.invalidateQueries({ queryKey: keys.balances });
    qc.invalidateQueries({ queryKey: keys.catalog });
  };
}

export type { BalanceReport, ClaimReport, LoginSession, RefreshReport };
