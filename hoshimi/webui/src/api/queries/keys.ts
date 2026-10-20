/** Query keys, shared by the query modules. */
export const keys = {
  meta: ['app', 'meta'] as const,
  version: ['app', 'version'] as const,
  health: ['app', 'health'] as const,
  settings: ['settings'] as const,
  providers: ['providers'] as const,
  network: ['network', 'diagnostics'] as const,
  catalog: ['catalog'] as const,
  stats: ['stats'] as const,
  probe: ['probe'] as const,
  balances: ['credits', 'balances'] as const,
  refresh: ['refresh', 'status'] as const,
  loginProviders: ['login', 'providers'] as const,
  stations: ['stations'] as const,
  providerForms: ['providerForms'] as const,
};
