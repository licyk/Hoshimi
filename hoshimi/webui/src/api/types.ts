import type { components } from '@/api/schema';

/** Shorthand for the generated schemas. */
export type S = components['schemas'];

export type SettingsView = S['SettingsView'];
export type ServerView = S['ServerView'];
export type ProviderView = S['ProviderView'];
export type AppMeta = S['AppMeta'];
export type ProviderSummary = S['ProviderSummary'];
export type ModelList = S['ModelList'];
export type ModelObject = S['ModelObject'];

export type UsageTotals = S['UsageTotals'];
export type UsageReport = S['UsageReport'];
export type UsagePoint = S['UsagePoint'];
export type UsageBreakdownRow = S['UsageBreakdownRow'];
export type CallRecord = S['CallRecord'];
export type CallPage = S['CallPage'];
export type LiveCall = S['LiveCall'];
export type ChannelsReport = S['ChannelsReport'];
export type ChannelView = S['ChannelView'];
export type ChannelAccount = S['ChannelAccount'];

/** One frame of ``/api/v1/stats/live``. */
export type LiveEvent =
  | { type: 'snapshot'; server_time: number; active: LiveCall[]; today: UsageTotals; recent: CallRecord[] }
  | { type: 'started' | 'progress'; call: LiveCall }
  | { type: 'finished'; call_id: string; record: CallRecord; today: UsageTotals };
