import { useQuery, useQueryClient } from '@tanstack/vue-query';
import { computed, type Ref } from 'vue';
import { api, unwrap } from '@/api/client';
import { keys } from '@/api/queries/keys';

/**
 * The Playground's kept conversations. The page calls these as a conversation happens (create on
 * the first send, append each message, remember which one is open); the history list is a query
 * the calls invalidate, so it follows without being refetched on its own.
 */
export const conversationsApi = {
  create: (model: string, effort: string) => unwrap(api.POST('/api/v1/playground/conversations', { body: { model, effort, title: '' } })),
  get: (id: string) => unwrap(api.GET('/api/v1/playground/conversations/{conversation_id}', { params: { path: { conversation_id: id } } })),
  update: (id: string, patch: { title?: string; model?: string; effort?: string; archived?: boolean }) =>
    unwrap(api.PATCH('/api/v1/playground/conversations/{conversation_id}', { params: { path: { conversation_id: id } }, body: patch })),
  remove: (id: string) => unwrap(api.DELETE('/api/v1/playground/conversations/{conversation_id}', { params: { path: { conversation_id: id } } })),
  append: (id: string, role: 'user' | 'assistant', content: string, model = '') =>
    unwrap(api.POST('/api/v1/playground/conversations/{conversation_id}/messages', { params: { path: { conversation_id: id } }, body: { role, content, model } })),
  session: () => unwrap(api.GET('/api/v1/playground/session')),
  setSession: (id: string | null) => unwrap(api.PUT('/api/v1/playground/session', { body: { conversation_id: id } })),
};

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
