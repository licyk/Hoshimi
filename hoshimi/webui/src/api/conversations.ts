import { useQuery, useQueryClient } from '@tanstack/vue-query';
import { computed, type Ref } from 'vue';
import { authedFetch, readSse } from '@/api/chat';
import { api, unwrap } from '@/api/client';
import { keys } from '@/api/queries/keys';
import type { ApprovalDecision, ApprovalMode, AttachmentView, CompactionStep, MessageView, Notice, ReplyUsage, ToolGroup, ToolStep, UserStep } from '@/api/types';

/**
 * The Playground's kept conversations. A conversation is a tree of messages; sending, editing and
 * retrying add messages and stream the reply back as events (``ReplyEvent``), while the server puts
 * together what the model is sent. The history list is a query these calls invalidate.
 */
export type ReplyEvent =
  | { type: 'user'; message: MessageView }
  | { type: 'assistant'; message: MessageView }
  | { type: 'stage'; stage: 'describing'; model: string }
  | { type: 'notice'; notice: Notice }
  | { type: 'reasoning'; text: string }
  | { type: 'delta'; text: string }
  | { type: 'tool'; step: ToolStep }
  | { type: 'steer'; step: UserStep }
  /** What the reply has taken after a round, against the model's window and where it is compacted. */
  | { type: 'usage'; usage: ReplyUsage; window: number; limit: number }
  | { type: 'compaction'; step: CompactionStep }
  /** The user's own compaction, filed while its summary is written. */
  | { type: 'checkpoint'; message: MessageView }
  | { type: 'done'; message: MessageView };

export interface SendBody {
  parent_id: string | null;
  content: string;
  attachment_ids: string[];
  model: string;
  effort: string;
}

async function stream(path: string, body: unknown, onEvent: (event: ReplyEvent) => void, signal?: AbortSignal): Promise<void> {
  const response = await authedFetch(path, { method: 'POST', headers: { Accept: 'text/event-stream' }, json: body, signal });
  await readSse(response, (payload) => onEvent(JSON.parse(payload) as ReplyEvent));
}

const path = (id: string) => ({ params: { path: { conversation_id: id } } });

export const conversationsApi = {
  create: (model: string, effort: string, temporary = false, tools: { on: ToolGroup[]; off: ToolGroup[] } = { on: [], off: [] }, approval: ApprovalMode | '' = '') =>
    unwrap(api.POST('/api/v1/playground/conversations', { body: { model, effort, temporary, title: '', tools_on: tools.on, tools_off: tools.off, approval } })),
  get: (id: string) => unwrap(api.GET('/api/v1/playground/conversations/{conversation_id}', path(id))),
  update: (id: string, patch: { title?: string; model?: string; effort?: string; archived?: boolean; current_leaf?: string; tools_off?: ToolGroup[]; tools_on?: ToolGroup[]; approval?: ApprovalMode | '' }) =>
    unwrap(api.PATCH('/api/v1/playground/conversations/{conversation_id}', { ...path(id), body: patch })),
  remove: (id: string) => unwrap(api.DELETE('/api/v1/playground/conversations/{conversation_id}', path(id))),
  branch: (id: string, messageId: string) =>
    unwrap(api.POST('/api/v1/playground/conversations/{conversation_id}/branch', { ...path(id), body: { message_id: messageId } })),
  title: (id: string) => unwrap(api.POST('/api/v1/playground/conversations/{conversation_id}/title', path(id))),
  /** The Chat Completions request behind a reply, as the gateway sent it. */
  request: (id: string, messageId: string) =>
    unwrap(api.GET('/api/v1/playground/conversations/{conversation_id}/messages/{message_id}/request', { params: { path: { conversation_id: id, message_id: messageId } } })),
  /** Answer a tool call that waits for approval in the reply streaming now. */
  approve: (id: string, callId: string, decision: ApprovalDecision) =>
    unwrap(api.POST('/api/v1/playground/conversations/{conversation_id}/approvals/{call_id}', { params: { path: { conversation_id: id, call_id: callId } }, body: { decision } })),
  /** A message for the reply under way, read at its next round; a 409 when no reply takes one. */
  steer: (id: string, content: string) =>
    unwrap(api.POST('/api/v1/playground/conversations/{conversation_id}/steer', { ...path(id), body: { content } })),
  /** The background commands this conversation's tools started. */
  terminals: (id: string) => unwrap(api.GET('/api/v1/playground/conversations/{conversation_id}/terminals', path(id))),
  stopTerminals: (id: string) => unwrap(api.DELETE('/api/v1/playground/conversations/{conversation_id}/terminals', path(id))),
  stopTerminal: (id: string, sessionId: number) =>
    unwrap(api.DELETE('/api/v1/playground/conversations/{conversation_id}/terminals/{session_id}', { params: { path: { conversation_id: id, session_id: sessionId } } })),
  /** How full the conversation on screen is against its model's window, and what its replies took. */
  context: (id: string) => unwrap(api.GET('/api/v1/playground/conversations/{conversation_id}/context', path(id))),
  session: () => unwrap(api.GET('/api/v1/playground/session')),
  setSession: (id: string | null) => unwrap(api.PUT('/api/v1/playground/session', { body: { conversation_id: id } })),

  /** Add a question after ``parent_id`` and stream the reply. */
  send: (id: string, body: SendBody, onEvent: (event: ReplyEvent) => void, signal?: AbortSignal) =>
    stream(`/api/v1/playground/conversations/${encodeURIComponent(id)}/messages`, body, onEvent, signal),
  /** Another reply beside ``messageId`` (or a first one to a question), streamed. */
  retry: (id: string, messageId: string, body: { model?: string; effort?: string }, onEvent: (event: ReplyEvent) => void, signal?: AbortSignal) =>
    stream(`/api/v1/playground/conversations/${encodeURIComponent(id)}/messages/${encodeURIComponent(messageId)}/retry`, body, onEvent, signal),

  /** Compact the conversation on screen now (a checkpoint after it, holding the summary), streamed. */
  compact: (id: string, instructions: string, onEvent: (event: ReplyEvent) => void, signal?: AbortSignal) =>
    stream(`/api/v1/playground/conversations/${encodeURIComponent(id)}/compact`, { instructions }, onEvent, signal),

  /** An image as itself, or a file as its text (with ``type`` the file's own, for a PDF read in the browser). */
  async upload(data: Blob, name: string, query: { type?: string; width?: number; height?: number } = {}): Promise<AttachmentView> {
    const params = new URLSearchParams({ name });
    for (const [key, value] of Object.entries(query)) if (value !== undefined && value !== '') params.set(key, String(value));
    const response = await authedFetch(`/api/v1/playground/attachments?${params}`, { method: 'POST', headers: { 'Content-Type': data.type || 'text/plain' }, body: data });
    return (await response.json()) as AttachmentView;
  },
  removeAttachment: (id: string) => unwrap(api.DELETE('/api/v1/playground/attachments/{attachment_id}', { params: { path: { attachment_id: id } } })),
};

const blobs = new Map<string, Promise<string>>();
/**
 * An attachment as an object URL, fetched once with the access key (an ``<img>`` cannot send it).
 * Kept for the page's life: a conversation shows the same images again as it is reopened.
 */
export function attachmentUrl(id: string): Promise<string> {
  let url = blobs.get(id);
  if (!url) {
    url = authedFetch(`/api/v1/playground/attachments/${encodeURIComponent(id)}`, { method: 'GET' })
      .then((response) => response.blob())
      .then((blob) => URL.createObjectURL(blob));
    url.catch(() => blobs.delete(id));
    blobs.set(id, url);
  }
  return url;
}

/** An attachment just uploaded from this page: its local preview stands in, so it is not fetched back. */
export function rememberAttachment(id: string, url: string) {
  blobs.set(id, Promise.resolve(url));
}

/** Recent or archived conversations, the most recently used first. */
export const useConversations = (archived: Ref<boolean>) =>
  useQuery({
    queryKey: computed(() => keys.conversationList(archived.value)),
    queryFn: () => unwrap(api.GET('/api/v1/playground/conversations', { params: { query: { archived: archived.value } } })),
  });

/** Marks both lists stale after a change, so the one in view refetches. */
export function useRefreshConversations() {
  const qc = useQueryClient();
  return () => qc.invalidateQueries({ queryKey: keys.conversations });
}

