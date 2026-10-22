import type { components } from '@/api/schema';

/** Shorthand for the generated schemas. */
export type S = components['schemas'];

export type SettingsView = S['SettingsView'];
export type ServerView = S['ServerView'];
export type ProviderView = S['ProviderView'];
export type AppMeta = S['AppMeta'];
export type UpdateInfo = S['UpdateInfo'];
export type ConversationSummary = S['ConversationSummary'];
export type ConversationView = S['ConversationView'];
export type MessageView = S['MessageView'];
export type AttachmentView = S['AttachmentView'];
export type Notice = S['Notice'];
export type TerminalView = S['TerminalView'];

/** A switch in Settings that turns on one of the Playground's tools (``terminal`` covers the background commands). */
export type ToolGroup = 'read' | 'apply_patch' | 'shell' | 'terminal' | 'web_search' | 'web_fetch';
export const TOOL_GROUPS: ToolGroup[] = ['read', 'apply_patch', 'shell', 'terminal', 'web_search', 'web_fetch'];

/** One step of a reply that used tools (``MessageView.steps``), in the order it happened. */
export type ReasoningStep = { type: 'reasoning'; text: string; ms: number; round: number };
export type TextStep = { type: 'text'; text: string; round: number };
export type ToolStatus = 'pending' | 'waiting' | 'running' | 'done' | 'error' | 'denied' | 'stopped';
export type ToolStep = {
  type: 'tool';
  id: string;
  name: string;
  /** The call's arguments as the model wrote them: JSON, while ``pending`` still coming in. */
  arguments: string;
  status: ToolStatus;
  output: string;
  exit_code: number | null;
  ms: number;
  /** What the tool says beside its output: ``path``, ``files``, ``session_id``, ``url``, ``query``… */
  meta: Record<string, unknown>;
  /** Why it waits for approval (``waiting``). */
  approval: string;
  round: number;
};
export type Step = ReasoningStep | TextStep | ToolStep;
export type ApprovalDecision = 'allow' | 'always' | 'deny';
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
export type CheckinRecord = S['CheckinRecord'];

/** One frame of ``/api/v1/stats/live``. */
export type LiveEvent =
  | { type: 'snapshot'; server_time: number; active: LiveCall[]; today: UsageTotals; recent: CallRecord[] }
  | { type: 'started' | 'progress'; call: LiveCall }
  | { type: 'finished'; call_id: string; record: CallRecord; today: UsageTotals };
