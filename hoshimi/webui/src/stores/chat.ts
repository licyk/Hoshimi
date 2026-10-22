import { defineStore } from 'pinia';
import { computed, reactive, ref } from 'vue';
import { conversationsApi, type ReplyEvent } from '@/api/conversations';
import type { AttachmentView, ConversationView, MessageView } from '@/api/types';

type Summary = Omit<ConversationView, 'messages'>;

/** What is happening before a reply's first word: the model is reasoning, or an image is being described. */
export type Stage = { kind: 'thinking' } | { kind: 'describing'; model: string } | null;

let localIds = 0;
const localId = (kind: string) => `local-${kind}-${++localIds}`;
export const isLocal = (id: string) => id.startsWith('local-');

/**
 * The Playground's open conversation: its message tree, the path on screen, and what can be done to
 * it. A conversation is created on its first send; a temporary one is deleted when it is left.
 * Replies stream into a placeholder that the server's own messages replace as they are filed, so
 * the page never waits to show what was asked. Failures before the question is kept are thrown
 * (the page puts the draft back); later ones end in the reply itself, as an error to retry.
 */
export const useChatStore = defineStore('chat', () => {
  const conversation = ref<Summary | null>(null);
  const messages = reactive(new Map<string, MessageView>());
  const leaf = ref('');
  /** The next new conversation is temporary (and the open one is, if it was started so). */
  const temporary = ref(false);
  const model = ref<string | null>(null);
  const effort = ref('');
  const busy = ref(false);
  const stage = ref<Stage>(null);
  /** Bumped whenever the history list should be read again. */
  const listChanged = ref(0);
  let controller: AbortController | null = null;

  /** Each message's replies or versions, oldest first, by the id they follow ('' for the first). */
  const children = computed(() => {
    const out = new Map<string, MessageView[]>();
    for (const m of messages.values()) {
      const key = m.parent_id ?? '';
      const list = out.get(key);
      if (list) list.push(m);
      else out.set(key, [m]);
    }
    for (const list of out.values()) list.sort((a, b) => a.created_at - b.created_at);
    return out;
  });
  /** The messages from the first to the current leaf: what is on screen, and what a model is sent. */
  const path = computed(() => {
    const out: MessageView[] = [];
    let current = messages.get(leaf.value);
    while (current) {
      out.push(current);
      current = current.parent_id ? messages.get(current.parent_id) : undefined;
    }
    return out.reverse();
  });
  const siblings = (m: MessageView) => children.value.get(m.parent_id ?? '') ?? [m];
  /** The newest message at the end of the newest replies under ``id``: where moving to a version lands. */
  function leafUnder(id: string): string {
    let current = id;
    for (let kids = children.value.get(current); kids?.length; kids = children.value.get(current)) current = kids[kids.length - 1].id;
    return current;
  }

  function load(view: ConversationView) {
    const { messages: list, ...summary } = view;
    conversation.value = summary;
    messages.clear();
    for (const m of list) messages.set(m.id, m);
    leaf.value = view.current_leaf;
    temporary.value = view.temporary;
    if (view.model) model.value = view.model;
    effort.value = view.effort;
  }

  /** A temporary conversation ends when it is left. */
  function leave() {
    stop();
    const left = conversation.value;
    if (left?.temporary) void conversationsApi.remove(left.id).catch(() => undefined);
  }

  async function open(id: string) {
    if (id === conversation.value?.id) return;
    const view = await conversationsApi.get(id);
    leave();
    load(view);
    if (!view.temporary) void conversationsApi.setSession(id).catch(() => undefined);
  }

  /** An empty page for a new conversation, temporary or not; it is created on its first send. */
  function startNew(options: { temporary?: boolean } = {}) {
    leave();
    conversation.value = null;
    messages.clear();
    leaf.value = '';
    temporary.value = !!options.temporary;
    void conversationsApi.setSession(null).catch(() => undefined);
  }

  async function ensure(): Promise<string> {
    if (conversation.value) return conversation.value.id;
    const view = await conversationsApi.create(model.value ?? '', effort.value, temporary.value);
    load(view);
    if (!view.temporary) {
      void conversationsApi.setSession(view.id).catch(() => undefined);
      listChanged.value++;
    }
    return view.id;
  }

  /** Follows a reply's events into the messages, from its placeholder to the reply as kept. */
  function follow(placeholders: { user?: string; reply: string }) {
    let reply = placeholders.reply;
    return (event: ReplyEvent) => {
      switch (event.type) {
        case 'user': {
          if (placeholders.user) messages.delete(placeholders.user);
          messages.set(event.message.id, event.message);
          const waiting = messages.get(reply);
          if (waiting) messages.set(reply, { ...waiting, parent_id: event.message.id });
          break;
        }
        case 'assistant': {
          const waiting = messages.get(reply);
          messages.delete(reply);
          reply = event.message.id;
          messages.set(reply, { ...event.message, content: waiting?.content ?? '', notices: waiting?.notices ?? [] });
          leaf.value = reply;
          break;
        }
        case 'stage':
          stage.value = { kind: 'describing', model: event.model };
          break;
        case 'thinking':
          stage.value = { kind: 'thinking' };
          break;
        case 'notice': {
          const m = messages.get(reply);
          if (m) m.notices = [...m.notices, event.notice];
          break;
        }
        case 'delta': {
          stage.value = null;
          const m = messages.get(reply);
          if (m) m.content += event.text;
          break;
        }
        case 'done':
          messages.delete(reply);
          reply = event.message.id;
          messages.set(reply, event.message);
          leaf.value = reply;
          break;
      }
      return false;
    };
  }

  /**
   * Runs one streamed reply. A failure before the server kept the question takes the placeholders
   * back and is thrown; after it, the reply settles as stopped (the user stopped it) or as an error.
   */
  async function run(start: (signal: AbortSignal, onEvent: (event: ReplyEvent) => boolean) => Promise<void>, placeholders: { user?: string; reply: string }, before: string) {
    busy.value = true;
    stage.value = null;
    const local = new AbortController();
    controller = local;
    const onEvent = follow(placeholders);
    let kept = !placeholders.user;
    const tracking = (event: ReplyEvent) => {
      if (event.type === 'user') kept = true;
      return onEvent(event);
    };
    try {
      await start(local.signal, tracking);
    } catch (error) {
      const stopped = (error as Error).name === 'AbortError';
      if (!kept && !stopped) {
        if (placeholders.user) messages.delete(placeholders.user);
        messages.delete(placeholders.reply);
        leaf.value = before;
        throw error;
      }
      // The reply in flight: whatever id it has by now.
      const reply = [...messages.values()].find((m) => m.status === 'streaming');
      if (reply) messages.set(reply.id, { ...reply, status: stopped ? 'stopped' : 'error', error: stopped ? '' : (error as Error).message });
    } finally {
      if (controller === local) controller = null;
      busy.value = false;
      stage.value = null;
    }
    listChanged.value++;
    void title();
  }

  function placeholder(role: 'user' | 'assistant', parent: string | null, content = '', attachments: AttachmentView[] = []): MessageView {
    return {
      id: localId(role), parent_id: parent, role, content, attachments, notices: [], created_at: Date.now() / 1000,
      status: role === 'assistant' ? 'streaming' : 'complete', error: '', model: role === 'assistant' ? (model.value ?? '') : '', effort: '',
    };
  }

  /** A question after ``parent`` (by default, the end of the path), answered as it streams. */
  async function submit(content: string, attachments: AttachmentView[], parent: string | null = leaf.value || null) {
    if (busy.value) return;
    const before = leaf.value;
    const id = await ensure();
    const question = placeholder('user', parent, content, attachments);
    const reply = placeholder('assistant', question.id);
    messages.set(question.id, question);
    messages.set(reply.id, reply);
    leaf.value = reply.id;
    const body = { parent_id: parent, content, attachment_ids: attachments.map((a) => a.id), model: model.value ?? '', effort: effort.value };
    await run((signal, onEvent) => conversationsApi.send(id, body, onEvent, signal), { user: question.id, reply: reply.id }, before);
  }

  /** An edited question: a new version beside ``messageId``, answered afresh. */
  async function edit(messageId: string, content: string, attachments: AttachmentView[]) {
    const original = messages.get(messageId);
    if (!original) return;
    await submit(content, attachments, original.parent_id);
  }

  /** Another reply beside ``messageId`` (or a first one to a question), with another model if one is named. */
  async function retry(messageId: string, withModel?: string) {
    if (busy.value || !conversation.value) return;
    const target = messages.get(messageId);
    if (!target) return;
    const question = target.role === 'user' ? target.id : target.parent_id;
    if (!question) return;
    const before = leaf.value;
    const reply = placeholder('assistant', question);
    if (withModel) reply.model = withModel;
    messages.set(reply.id, reply);
    leaf.value = reply.id;
    const id = conversation.value.id;
    await run((signal, onEvent) => conversationsApi.retry(id, messageId, withModel ? { model: withModel } : {}, onEvent, signal), { reply: reply.id }, before);
  }

  function stop() {
    controller?.abort();
  }

  /** Show another version of a turn: the newest replies under it come along. */
  function switchTo(messageId: string) {
    if (busy.value) return;
    leaf.value = leafUnder(messageId);
    const id = conversation.value?.id;
    if (id) void conversationsApi.update(id, { current_leaf: leaf.value }).catch(() => undefined);
  }

  /** A new conversation up to ``messageId``, opened in place of this one. */
  async function branch(messageId: string) {
    const id = conversation.value?.id;
    if (!id) return;
    const view = await conversationsApi.branch(id, messageId);
    leave();
    load(view);
    void conversationsApi.setSession(view.id).catch(() => undefined);
    listChanged.value++;
  }

  async function rename(title: string) {
    const id = conversation.value?.id;
    if (!id || !title.trim()) return;
    const summary = await conversationsApi.update(id, { title });
    if (conversation.value?.id === id) conversation.value = { ...conversation.value, title: summary.title, title_state: summary.title_state };
    listChanged.value++;
  }

  /**
   * A model names the conversation once its first reply is in; a temporary or named one is left
   * alone. The server keeps the first question as the title until then, and says when it is done.
   */
  async function title() {
    const c = conversation.value;
    if (!c || c.temporary || c.title_state === 'auto' || c.title_state === 'user') return;
    if (!path.value.some((m) => m.role === 'assistant' && m.status === 'complete' && m.content)) return;
    try {
      const summary = await conversationsApi.title(c.id);
      if (conversation.value?.id === c.id) conversation.value = { ...conversation.value, title: summary.title, title_state: summary.title_state };
      listChanged.value++;
    } catch {
      /* the first question stays the title */
    }
  }

  /** The model or level picked for what is sent next; kept with the conversation. */
  function choose(next: { model?: string | null; effort?: string }) {
    if (next.model !== undefined) model.value = next.model;
    if (next.effort !== undefined) effort.value = next.effort;
    const id = conversation.value?.id;
    if (id) void conversationsApi.update(id, { model: model.value ?? '', effort: effort.value }).catch(() => undefined);
  }

  /** Something done to a conversation elsewhere (the history's menu): the page follows. */
  function forget(id: string) {
    if (conversation.value?.id === id) {
      conversation.value = null;
      messages.clear();
      leaf.value = '';
      temporary.value = false;
    }
  }

  return {
    conversation, messages, leaf, temporary, model, effort, busy, stage, listChanged, children, path,
    siblings, leafUnder, load, open, startNew, submit, edit, retry, stop, switchTo, branch, rename, title, choose, forget,
  };
});
