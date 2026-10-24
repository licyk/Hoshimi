import { defineStore } from 'pinia';
import { computed, reactive, ref } from 'vue';
import { conversationsApi, type OnEvent, type ReplyEvent } from '@/api/conversations';
import type { ApprovalDecision, ApprovalMode, AttachmentView, ContextView, ConversationView, MessageView, ReplyUsage, Step, TerminalView, ToolStep } from '@/api/types';

type Summary = Omit<ConversationView, 'messages'>;

/** What is happening before a reply starts: an image is being described for a model that cannot read it. */
export type Stage = { kind: 'describing'; model: string } | null;

let localIds = 0;
const localId = (kind: string) => `local-${kind}-${++localIds}`;
export const isLocal = (id: string) => id.startsWith('local-');

/** A reply's steps (``MessageView.steps``), typed. */
export const stepsOf = (m: MessageView) => (m.steps ?? []) as Step[];

/** More words or reasoning: onto the last step when it is of that kind, as a new step otherwise (as the server keeps them). */
function addTo(m: MessageView, type: 'text' | 'reasoning', text: string) {
  m.steps ??= [];
  const steps = stepsOf(m);
  const last = steps[steps.length - 1];
  if (last?.type === type) last.text += text;
  else steps.push(type === 'text' ? { type, text, round: 0 } : { type, text, ms: 0, round: 0 });
}
const UNSETTLED = new Set(['pending', 'reviewing', 'waiting', 'running']);

/**
 * The Playground's open conversation: its message tree, the path on screen, and what can be done to
 * it. A conversation is created on its first send; a temporary one is deleted when it is left.
 * Replies stream into a placeholder that the server's own messages replace as they are filed, so
 * the page never waits to show what was asked. Failures before the question is kept are thrown
 * (the page puts the draft back); later ones end in the reply itself, as an error to retry.
 *
 * A reply works on the server, not in the page: leaving its conversation (for another, or a new one)
 * only stops following it, and it goes on. Opening a conversation whose reply (or compaction) is
 * under way follows it again from its first event. Stop asks the server to stop it; the reply's own
 * end comes back as it settles.
 */
export const useChatStore = defineStore('chat', () => {
  const conversation = ref<Summary | null>(null);
  const messages = reactive(new Map<string, MessageView>());
  const leaf = ref('');
  /** The next new conversation is temporary (and the open one is, if it was started so). */
  const temporary = ref(false);
  const model = ref<string | null>(null);
  const effort = ref('');
  /** When its tools ask first; empty: as Settings say. */
  const approval = ref<ApprovalMode | ''>('');
  /** Messages sent to the reply under way that it has not read yet. */
  const queued = ref<{ id: string; text: string }[]>([]);
  /** The background commands its tools started. */
  const terminals = ref<TerminalView[]>([]);
  const busy = ref(false);
  /** The conversation is being compacted on the user's word (``busy`` too: Stop ends it). */
  const compacting = ref(false);
  /** When the reply under way started, by this page's clock: its timer counts from here. */
  const startedAt = ref(0);
  /** How full the conversation is (``/status``, the composer's ring): as the server last said, then as the reply under way counts. */
  const context = ref<ContextView | null>(null);
  /** What the reply under way has taken, after each of its rounds. */
  const live = ref<{ usage: ReplyUsage; window: number; limit: number } | null>(null);
  const stage = ref<Stage>(null);
  /** Bumped whenever the history list should be read again. */
  const listChanged = ref(0);
  let controller: AbortController | null = null;
  /** Which following is the page's: one left behind (another conversation opened) reads no more and changes nothing. */
  let following = 0;
  /** Times in a row a followed reply's stream ended before its end came, and the page went to look. */
  let lost = 0;

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
    approval.value = view.approval ?? '';
    context.value = null;
    if (view.running) attach(view);
    void refreshTerminals();
    void refreshContext();
  }

  /** Stop following the reply under way, which goes on on the server: nothing it sends from now on changes this page. */
  function detach() {
    following++;
    controller?.abort();
    controller = null;
    busy.value = compacting.value = false;
    stage.value = null;
    live.value = null;
    queued.value = [];
  }

  /**
   * Follow the reply (or compaction) under way in a conversation just opened, from its first event:
   * what the page has of it (as it was filed, before a word) is replaced as the events come again.
   */
  function attach(view: ConversationView) {
    const id = view.id;
    const working = [...view.messages].reverse().find((m) => m.status === 'streaming');
    const gone = (error: unknown) => {
      // It ended between the conversation and its events: the page looks again.
      if ((error as { status?: number }).status !== 404) throw error;
    };
    if (working?.role === 'checkpoint') {
      void runCompaction((signal, onEvent) => conversationsApi.follow(id, onEvent, signal).catch(gone), working.parent_id ?? '').catch(() => undefined);
    } else {
      if (working) {
        messages.set(working.id, { ...working, content: '', reasoning: '', notices: [], steps: [] });
        leaf.value = working.id;
      }
      void run((signal, onEvent) => conversationsApi.follow(id, onEvent, signal).catch(gone), { reply: working?.id ?? localId('assistant') }, working?.parent_id ?? view.current_leaf);
    }
    // Its clock counts from when it started, not from when this page came back to it.
    if (working) startedAt.value = working.created_at * 1000;
  }

  /** The open conversation as the server has it now, followed again if its reply still works. */
  async function reload() {
    const id = conversation.value?.id;
    if (!id || isLocal(id)) return;
    try {
      const view = await conversationsApi.get(id);
      if (conversation.value?.id !== id) return;
      detach();
      load(view);
    } catch {
      /* it stays as the page has it */
    }
  }

  /** Leaving a conversation stops following its reply, which goes on; a temporary conversation ends (and its reply with it). */
  function leave() {
    detach();
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
    // A new chat starts as Settings say when its tools ask first.
    approval.value = '';
    terminals.value = [];
    context.value = null;
    void conversationsApi.setSession(null).catch(() => undefined);
  }

  async function ensure(): Promise<string> {
    if (conversation.value) return conversation.value.id;
    const view = await conversationsApi.create(model.value ?? '', effort.value, temporary.value, approval.value);
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
          messages.set(reply, { ...event.message, content: waiting?.content ?? '', reasoning: waiting?.reasoning ?? '', notices: waiting?.notices ?? [], steps: waiting?.steps ?? [] });
          leaf.value = reply;
          break;
        }
        case 'stage':
          stage.value = { kind: 'describing', model: event.model };
          break;
        case 'reasoning': {
          stage.value = null;
          const m = messages.get(reply);
          if (m) {
            m.reasoning += event.text;
            addTo(m, 'reasoning', event.text);
          }
          break;
        }
        case 'notice': {
          const m = messages.get(reply);
          if (m) m.notices = [...m.notices, event.notice];
          break;
        }
        case 'delta': {
          stage.value = null;
          const m = messages.get(reply);
          if (m) {
            m.content += event.text;
            addTo(m, 'text', event.text);
          }
          break;
        }
        case 'steer': {
          queued.value = queued.value.filter((q) => q.id !== event.step.id);
          const m = messages.get(reply);
          if (m) {
            m.steps ??= [];
            stepsOf(m).push(event.step);
          }
          break;
        }
        case 'tool':
        case 'compaction': {
          stage.value = null;
          const m = messages.get(reply);
          if (!m) break;
          m.steps ??= [];
          const steps = stepsOf(m);
          const at = steps.findIndex((s) => s.type === event.step.type && s.id === event.step.id);
          if (at >= 0) steps[at] = event.step;
          else steps.push(event.step);
          if (event.type === 'tool' && ['exec_command', 'write_stdin', 'kill_session'].includes(event.step.name) && !UNSETTLED.has(event.step.status)) void refreshTerminals();
          break;
        }
        case 'usage': {
          live.value = { usage: event.usage, window: event.window, limit: event.limit };
          const m = messages.get(reply);
          if (m) m.usage = event.usage;
          if (context.value && event.usage.context !== undefined) {
            context.value = { ...context.value, used: event.usage.context, used_source: event.usage.source ?? 'estimate', window: event.window, limit: event.limit };
          }
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
  async function run(start: (signal: AbortSignal, onEvent: OnEvent) => Promise<void>, placeholders: { user?: string; reply: string }, before: string) {
    const mine = ++following;
    busy.value = true;
    stage.value = null;
    startedAt.value = Date.now();
    live.value = null;
    const local = new AbortController();
    controller = local;
    const onEvent = follow(placeholders);
    let kept = !placeholders.user;
    /** The reply's end came (``done``), or its stream failed and the reply settled here: no need to look again. */
    let ended = false;
    const tracking = (event: ReplyEvent) => {
      // Let go (another conversation opened): stop reading; the reply goes on without this page.
      if (mine !== following) return true;
      if (event.type === 'user') kept = true;
      if (event.type === 'assistant') listChanged.value++;
      if (event.type === 'done') ended = true;
      return onEvent(event);
    };
    try {
      await start(local.signal, tracking);
    } catch (error) {
      if (mine !== following) return;
      ended = true;
      const stopped = (error as Error).name === 'AbortError';
      if (!kept && !stopped) {
        if (placeholders.user) messages.delete(placeholders.user);
        messages.delete(placeholders.reply);
        leaf.value = before;
        throw error;
      }
      // The reply in flight: whatever id it has by now.
      const reply = [...messages.values()].find((m) => m.status === 'streaming');
      if (reply) {
        // What the server settles as stopped: the calls the stop cut short.
        const steps = stepsOf(reply).map((s) => (s.type === 'tool' && UNSETTLED.has(s.status) ? { ...s, status: 'stopped' as const } : s));
        messages.set(reply.id, {
          ...reply, steps, status: stopped ? 'stopped' : 'error', error: stopped ? '' : (error as Error).message,
          // As this page counted it: the server's own count did not come.
          duration_ms: Date.now() - startedAt.value, finished_at: Date.now() / 1000,
        });
      }
    } finally {
      if (controller === local) controller = null;
      if (mine === following) {
        busy.value = false;
        stage.value = null;
        live.value = null;
      }
    }
    if (mine !== following) return;
    listChanged.value++;
    if (ended) lost = 0;
    else if (lost++ < 3) {
      // The stream ended before the reply did (the connection dropped): look again, and follow it if it still works.
      return void reload();
    }
    void title();
    void refreshTerminals();
    void refreshContext();
  }

  function placeholder(role: 'user' | 'assistant', parent: string | null, content = '', attachments: AttachmentView[] = []): MessageView {
    return {
      id: localId(role), parent_id: parent, role, content, attachments, notices: [], created_at: Date.now() / 1000,
      status: role === 'assistant' ? 'streaming' : 'complete', error: '', model: role === 'assistant' ? (model.value ?? '') : '', effort: '', reasoning: '', reasoning_ms: 0,
      steps: [], usage: {}, duration_ms: 0, waited_ms: 0, finished_at: 0,
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

  /** Another reply beside ``messageId`` (or a first one to a question), from ``withModel`` and ``withEffort`` when given. */
  async function retry(messageId: string, withModel?: string, withEffort?: string) {
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
    const body = { ...(withModel ? { model: withModel } : {}), ...(withEffort !== undefined ? { effort: withEffort } : {}) };
    await run((signal, onEvent) => conversationsApi.retry(id, messageId, body, onEvent, signal), { reply: reply.id }, before);
  }

  /** Stop the reply (or compaction) under way, on the server: its end comes back as it settles. */
  function stop() {
    const id = conversation.value?.id;
    if (!busy.value) return;
    if (!id || isLocal(id)) return void controller?.abort();
    // The server cannot be told: the page stops reading at least, and says the reply stopped.
    conversationsApi.stop(id).catch(() => controller?.abort());
  }

  /**
   * Compact the conversation now: a checkpoint after the path on screen, holding a model's summary of
   * it, which the model is sent from then on in place of what came before. A failure (or nothing to
   * compact) is thrown, with the conversation as it was; Stop ends it the same way.
   */
  async function compact(instructions = '') {
    const id = conversation.value?.id;
    if (!id || busy.value) return;
    return runCompaction((signal, onEvent) => conversationsApi.compact(id, instructions, onEvent, signal), leaf.value);
  }

  /** Follows a compaction (the user's, just asked for or under way when the conversation was opened) to its end; true when it was kept. */
  async function runCompaction(start: (signal: AbortSignal, onEvent: OnEvent) => Promise<void>, before: string): Promise<boolean> {
    const mine = ++following;
    busy.value = compacting.value = true;
    startedAt.value = Date.now();
    const local = new AbortController();
    controller = local;
    let checkpoint = '';
    let failure = '';
    try {
      await start(local.signal, (event) => {
        if (mine !== following) return true;
        if (event.type === 'checkpoint' || (event.type === 'done' && event.message.status !== 'error')) {
          messages.set(event.message.id, event.message);
          checkpoint = event.message.id;
          leaf.value = event.message.id;
          if (event.type === 'checkpoint') listChanged.value++;
        } else if (event.type === 'done') {
          failure = event.message.error;
        }
      });
    } catch (error) {
      if (mine !== following) return false;
      // Stopped: quietly, as it was. Refused before it began (nothing to compact, a reply running): thrown as it came.
      const stopped = (error as Error).name === 'AbortError';
      if (!stopped && !checkpoint) throw error;
      if (!stopped) failure = (error as Error).message;
    } finally {
      if (controller === local) controller = null;
      if (mine === following) busy.value = compacting.value = false;
    }
    if (mine !== following) return false;
    listChanged.value++;
    const kept = messages.get(checkpoint);
    if (!kept || kept.status !== 'complete') {
      // Failed or stopped: the server took the checkpoint back.
      if (checkpoint) messages.delete(checkpoint);
      leaf.value = before;
      if (failure) throw new Error(failure);
      return false;
    }
    void refreshContext();
    return true;
  }

  async function refreshContext() {
    const id = conversation.value?.id;
    if (!id || isLocal(id)) {
      context.value = null;
      return;
    }
    try {
      const view = await conversationsApi.context(id);
      if (conversation.value?.id === id) context.value = view;
    } catch {
      /* the numbers stay as they were */
    }
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
    // The status follows the model: its window, and what it is told.
    if (id) void conversationsApi.update(id, { model: model.value ?? '', effort: effort.value }).then(refreshContext, () => undefined);
  }

  /**
   * A message for the reply under way, read at its next round. False when no reply takes it (none
   * runs, it has no tools, or it has just ended): the caller keeps the text to send on its own.
   */
  async function steer(text: string): Promise<boolean> {
    const id = conversation.value?.id;
    if (!id || !busy.value) return false;
    try {
      const { id: steerId } = await conversationsApi.steer(id, text);
      // Read already (the event can beat the answer to this request), or waiting to be.
      if (!stepsOf(messages.get(leaf.value) ?? ({} as MessageView)).some((s) => s.type === 'user' && s.id === steerId)) queued.value = [...queued.value, { id: steerId, text }];
      return true;
    } catch (error) {
      if ((error as { status?: number }).status === 409) return false;
      throw error;
    }
  }

  /** What was sent to a reply that ended before reading it, given back once (to put in the composer). */
  function takeUnread(): string[] {
    const texts = queued.value.map((q) => q.text);
    queued.value = [];
    return texts;
  }

  /** When this chat's tools ask first; it holds from the next call on, even mid-reply. */
  function chooseApproval(mode: ApprovalMode) {
    approval.value = mode;
    const id = conversation.value?.id;
    if (id) void conversationsApi.update(id, { approval: mode }).catch(() => undefined);
  }

  /** The user's answer to a call that waits; the reply's own events show what came of it. */
  async function approve(step: ToolStep, decision: ApprovalDecision) {
    const id = conversation.value?.id;
    if (!id) return;
    await conversationsApi.approve(id, step.id, decision);
  }

  async function refreshTerminals() {
    const id = conversation.value?.id;
    if (!id || isLocal(id)) {
      terminals.value = [];
      return;
    }
    try {
      const list = (await conversationsApi.terminals(id)).terminals;
      if (conversation.value?.id === id) terminals.value = list;
    } catch {
      /* the list stays as it was */
    }
  }

  async function stopTerminal(sessionId: number) {
    const id = conversation.value?.id;
    if (!id) return;
    await conversationsApi.stopTerminal(id, sessionId).catch(() => undefined);
    await refreshTerminals();
  }

  /** Something done to a conversation elsewhere (the history's menu): the page follows. */
  function forget(id: string) {
    if (conversation.value?.id === id) {
      detach();
      conversation.value = null;
      messages.clear();
      leaf.value = '';
      temporary.value = false;
      terminals.value = [];
      context.value = null;
    }
  }

  return {
    conversation, messages, leaf, temporary, model, effort, approval, queued, terminals, busy, compacting, startedAt, context, live, stage, listChanged,
    children, path, siblings, leafUnder, load, reload, open, startNew, submit, edit, retry, stop, detach, compact, refreshContext, switchTo, branch, rename, title, choose,
    chooseApproval, steer, takeUnread, approve, refreshTerminals, stopTerminal, forget,
  };
});
