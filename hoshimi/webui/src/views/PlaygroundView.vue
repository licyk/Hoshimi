<script setup lang="ts">
import { computed, nextTick, onActivated, onBeforeUnmount, onDeactivated, onMounted, provide, ref, watch } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import { BASE_URL } from '@/api/baseUrl';
import { conversationsApi, rememberAttachment, useRefreshConversations } from '@/api/conversations';
import { useModelSpecs, useModels, useSettings } from '@/api/queries/app';
import { APPROVAL_MODES, TOOL_GROUPS, type ApprovalDecision, type ApprovalMode, type AttachmentView, type CompactionStep, type ContextView, type ConversationSummary, type MessageView, type ToolStep } from '@/api/types';
import CatalogStatus from '@/components/CatalogStatus.vue';
import AssistantMessage from '@/components/chat/AssistantMessage.vue';
import AttachmentPreview from '@/components/chat/AttachmentPreview.vue';
import { AttachmentError, prepare } from '@/components/chat/attachments';
import ChatSidebar, { type ConversationAction } from '@/components/chat/ChatSidebar.vue';
import CompactionMarker from '@/components/chat/CompactionMarker.vue';
import ContextRing from '@/components/chat/ContextRing.vue';
import { find, parseDraft, unavailable, type SlashContext, type SlashId, type SlashPage } from '@/components/chat/slash/commands';
import SlashMenu from '@/components/chat/SlashMenu.vue';
import UserMessage from '@/components/chat/UserMessage.vue';
import VersionPanes from '@/components/chat/VersionPanes.vue';
import { useI18n } from '@/i18n';
import { isLocal, stepsOf, useChatStore } from '@/stores/chat';
import { usePreferencesStore } from '@/stores/preferences';
import { AppButton, AppDialog, AppIcon, AppMenu, ChipButton, Composer, ComposerMenu, ConfirmDialog, EmptyState, FadeSwap, IconButton, ImageViewer, SideSheet, TRANSITIONS, TextField, chipRowHooks, icons, useImageViewer, useMorphWidth, useSnackbar } from '@/ui';
import { MARKDOWN_CONTEXT, type MarkdownLabels } from '@/ui/markdownRich';
import { useElementHeight } from '@/ui/useElementHeight';
import { useElementWidth } from '@/ui/useElementWidth';

/**
 * The Playground as a chat app: the chats in a list that slides out from the start (beside the chat
 * where there is room, a drawer where there is not), and the open one in a column with no names
 * beside its messages — the questions in bubbles at the end of the line, the replies across it.
 * The header holds the list's button at its start and, at its end, the chat's menu and one button
 * that starts what comes next: a new chat, or, on a new chat no one has written in yet, a temporary
 * one. The composer takes text, photos and files (by its + button, by dropping them on the page,
 * or by pasting), the model and its effort, and the tools the model may call (those Settings turned
 * on), with the commands they left running above it. Typing ``/`` opens its commands (the model, the
 * effort, compacting, the status, a new chat, a fork, the tools and when they ask first); a ring
 * beside the send button says how full the context window is. Everything said is kept on the
 * server, which also writes the titles, describes images for models that cannot read them, runs
 * the tools, and compacts a conversation that nears its model's window.
 */
const { t } = useI18n();
const route = useRoute();
const router = useRouter();
const models = useModels();
const specs = useModelSpecs();
const snackbar = useSnackbar();
const chat = useChatStore();
const refreshList = useRefreshConversations();
watch(() => chat.listChanged, () => void refreshList());

// -- the model and its effort ------------------------------------------------
// A catalogue runs to hundreds of ids: the picker searches them, and names a model beside its id.
const modelOptions = computed(() =>
  (models.data.value?.data ?? []).map((m) => ({ value: m.id, label: m.id, description: [m.display_name !== m.id ? m.display_name : '', m.owned_by].filter(Boolean).join(' · '), keywords: [m.owned_by, m.display_name ?? ''] })),
);
watch(
  () => models.data.value?.data,
  (data) => {
    if (!chat.model && data?.length) chat.model = data[0].id;
  },
  { immediate: true },
);
// The models page opens the Playground to try one model: ?model=<id>, here or already kept alive. It
// starts a new chat with that model, and the address lets it go, so a reload returns to the session.
const openedForModel = typeof route.query.model === 'string' && !!route.query.model;
watch(
  () => route.query.model,
  (value) => {
    if (typeof value !== 'string' || !value) return;
    if (value !== chat.model || chat.path.length) {
      if (chat.path.length) chat.startNew();
      chat.choose({ model: value, effort: '' });
    }
    void router.replace({ query: {} });
  },
  { immediate: true },
);
const spec = computed(() => specs.data.value?.data.find((m) => m.id === chat.model));
const effortOptions = computed(() => [{ value: '', label: t('playground.effortAuto') }, ...(spec.value?.efforts ?? []).map((o) => ({ value: o.id, label: o.name }))]);
const effortText = computed(() => (chat.effort ? effortOptions.value.find((o) => o.value === chat.effort)?.label : t('playground.effortAutoShort')));

// -- tools ---------------------------------------------------------------------
// The tools every chat uses are the ones Settings turns on; only when they ask first is the chat's to choose.
const settings = useSettings();
const toolsOn = computed(() => TOOL_GROUPS.filter((g) => settings.data.value?.playground.tools.includes(g)));
// When this chat's tools ask first: as Settings say until the chat chooses for itself.
const defaultApproval = computed<ApprovalMode>(() => settings.data.value?.playground.approval ?? 'review');
const approval = computed<ApprovalMode>(() => chat.approval || defaultApproval.value);
const approvalOptions = computed(() => APPROVAL_MODES.map((m) => ({ value: m, label: t(`settings.approvalModes.${m}`), description: t(`settings.approvalModesHelp.${m}`) })));
const APPROVAL_ICONS = { review: icons.ShieldCheck, ask: icons.ShieldUser, workspace: icons.ShieldHalf, full: icons.ShieldOff };
// Steering: while a reply with tools runs, what is written goes to it, read at its next round.
const steerable = computed(() => chat.busy && !!chat.conversation && toolsOn.value.length > 0);
async function steerNow() {
  if (slashFirst()) return;
  const text = draft.value.trim();
  if (!text) return;
  draft.value = '';
  try {
    if (await chat.steer(text)) return void toBottom(true);
    draft.value = text;
    snackbar.show(t('playground.steerLate'));
  } catch (error) {
    draft.value = text;
    snackbar.error(error instanceof Error && error.message ? error.message : t('playground.sendFailed'));
  }
}
// A reply that ended (stopped, failed) before reading what was sent to it: the words come back to the box.
watch(
  () => chat.busy,
  (busy) => {
    if (busy) return;
    const unread = chat.takeUnread();
    if (unread.length) draft.value = [...unread, draft.value].filter((s) => s.trim()).join('\n\n');
  },
);
const runningTerminals = computed(() => chat.terminals.filter((term) => term.running));
// The composer's row of chips moves rather than jumps: the effort chip (and the context ring) open and
// close their width as they come and go (``chip-row``), the others slide over and a long model name
// narrows; a chip whose words change eases to its new width.
const effortSlot = ref<HTMLElement | null>(null);
const approvalSlot = ref<HTMLElement | null>(null);
useMorphWidth(effortSlot, () => effortText.value);
useMorphWidth(approvalSlot, () => approval.value);
async function approve(step: ToolStep, decision: ApprovalDecision) {
  try {
    await chat.approve(step, decision);
  } catch (error) {
    snackbar.error(error instanceof Error && error.message ? error.message : t('playground.approvalFailed'));
  }
}

// -- the layout ----------------------------------------------------------------
const root = ref<HTMLElement | null>(null);
// The page is never wider than the window: a phone starts without the sidebar rather than dropping it.
const width = useElementWidth(root, window.innerWidth);
/** Room for the sidebar beside the chat; on a narrower page it is a drawer. */
const wide = computed(() => width.value >= 900);
/** A phone's width: a long menu stands centred over the composer rather than over its button. */
const phone = computed(() => width.value < 600);
const SIDEBAR_KEY = 'hoshimi:playground-sidebar';
const sidebarOpen = ref(readSidebar());
function readSidebar() {
  try {
    return localStorage.getItem(SIDEBAR_KEY) !== 'closed';
  } catch {
    return true;
  }
}
watch(sidebarOpen, (open) => {
  try {
    localStorage.setItem(SIDEBAR_KEY, open ? 'open' : 'closed');
  } catch {
    /* private mode */
  }
});
const drawerOpen = ref(false);
/** The chat list: the panel beside the chat where there is room, the drawer where there is not. */
const listOpen = computed(() => (wide.value ? sidebarOpen.value : drawerOpen.value));
const toggleList = () => (wide.value ? (sidebarOpen.value = !sidebarOpen.value) : (drawerOpen.value = !drawerOpen.value));
/**
 * Until the page has been laid out, its width is a guess (the window's, the rail not taken out): the
 * sidebar takes its place without sliding, so it never slides away the moment the page appears.
 */
const laidOut = ref(false);
onMounted(() => requestAnimationFrame(() => (laidOut.value = true)));

// -- what replies show beyond text, and the image viewer ----------------------------------
// Replies render math, diagrams, media and HTML previews (``Markdown``); media on another site waits
// for a press unless Settings say to always load it. Any image here (an attachment, an image in a
// reply, a drawn diagram) opens in the viewer, with the others to move between.
const prefs = usePreferencesStore();
const MEDIA_KEYS = ['loadImage', 'loadVideo', 'loadAudio', 'code', 'diagram', 'image', 'preview', 'drawing', 'diagramFailed', 'previewTitle'] as const;
const markdownLabels = computed(
  () =>
    ({
      openImage: t('playground.openImage'), zoomIn: t('playground.viewer.zoomIn'), zoomOut: t('playground.viewer.zoomOut'), resetZoom: t('playground.viewer.resetZoom'),
      ...Object.fromEntries(MEDIA_KEYS.map((k) => [k, t(`playground.media.${k}`)])),
    }) as MarkdownLabels,
);
provide(MARKDOWN_CONTEXT, { remoteMedia: () => prefs.prefs.remoteMedia, labels: () => markdownLabels.value });
const main = ref<HTMLElement | null>(null);
const viewer = useImageViewer(() => main.value);
const VIEWER_KEYS = ['title', 'previous', 'next', 'zoomIn', 'zoomOut', 'resetZoom', 'download', 'openOriginal', 'counter', 'failed'] as const;
const viewerLabels = computed(() => ({ close: t('common.close'), ...Object.fromEntries(VIEWER_KEYS.map((k) => [k, t(`playground.viewer.${k}`)])) }));

// -- the thread ------------------------------------------------------------------
const scroller = ref<HTMLElement | null>(null);
const composer = ref<InstanceType<typeof Composer> | null>(null);
const empty = computed(() => !chat.path.length);
/** Follow the reply down while the reader is at the bottom; leave them where they scrolled to otherwise. */
let pinned = true;
const onScroll = () => {
  const el = scroller.value;
  if (el) pinned = el.scrollHeight - el.scrollTop - el.clientHeight < 80;
};
async function toBottom(force = false) {
  await nextTick();
  if (force || pinned) scroller.value?.scrollTo({ top: scroller.value.scrollHeight });
}
// A reply grows by its words, and by its tool calls as they come and change (one that asks must be seen).
watch(
  () => {
    const last = chat.path[chat.path.length - 1];
    const steps = last ? stepsOf(last) : [];
    const tail = steps[steps.length - 1];
    return [chat.path.length, last?.content.length, steps.length, tail?.type === 'tool' ? tail.status : ''];
  },
  () => void toBottom(),
);
watch(() => chat.conversation?.id, () => void toBottom(true));
// And by what opens inside it as it streams (a call that asks, opening to show what it would do).
const column = ref<HTMLElement | null>(null);
const columnHeight = useElementHeight(column);
watch(columnHeight, () => chat.busy && void toBottom());

const versionsOf = (m: MessageView) => {
  const list = chat.siblings(m);
  return { index: Math.max(0, list.findIndex((s) => s.id === m.id)), total: list.length };
};
function step(m: MessageView, direction: 1 | -1) {
  const list = chat.siblings(m);
  const target = list[list.findIndex((s) => s.id === m.id) + direction];
  if (target) chat.switchTo(target.id);
}

// -- the composer and what is attached ----------------------------------------
const draft = ref('');
interface Pending {
  key: number;
  kind: 'image' | 'file';
  name: string;
  bytes: number;
  url?: string;
  view?: AttachmentView;
}
let pendingKeys = 0;
const pending = ref<Pending[]>([]);
const uploading = computed(() => pending.value.some((p) => !p.view));
const attached = computed(() => pending.value.filter((p) => p.view).map((p) => p.view!));
const fileInput = ref<HTMLInputElement | null>(null);
const dragging = ref(false);
/** An image goes to a model that cannot read it as another model's description of it: say so before it goes. */
const visionHint = computed(() => spec.value && !spec.value.vision && pending.value.some((p) => p.kind === 'image'));

const reasons = { unsupported: 'playground.attachUnsupported', 'too-large': 'playground.attachTooLarge', 'no-text': 'playground.attachNoText', unreadable: 'playground.attachUnreadable' } as const;
async function add(files: Iterable<File>) {
  for (const file of files) {
    const entry: Pending = { key: ++pendingKeys, kind: file.type.startsWith('image/') ? 'image' : 'file', name: file.name, bytes: file.size };
    pending.value = [...pending.value, entry];
    try {
      const prepared = await prepare(file);
      const url = prepared.kind === 'image' ? URL.createObjectURL(prepared.blob) : undefined;
      update(entry.key, { kind: prepared.kind, name: prepared.name, bytes: prepared.blob.size, url });
      const view = await conversationsApi.upload(prepared.blob, prepared.name, { type: prepared.type, width: prepared.width, height: prepared.height });
      if (url) rememberAttachment(view.id, url);
      if (!pending.value.some((p) => p.key === entry.key)) {
        void conversationsApi.removeAttachment(view.id).catch(() => undefined);
        continue;
      }
      update(entry.key, { view, bytes: view.size });
    } catch (error) {
      drop(entry.key);
      snackbar.error(error instanceof AttachmentError ? t(reasons[error.reason], { name: file.name }) : t('playground.uploadFailed', { name: file.name }));
    }
  }
}
const update = (key: number, patch: Partial<Pending>) => (pending.value = pending.value.map((p) => (p.key === key ? { ...p, ...patch } : p)));
function drop(key: number) {
  const entry = pending.value.find((p) => p.key === key);
  pending.value = pending.value.filter((p) => p.key !== key);
  if (entry?.view) void conversationsApi.removeAttachment(entry.view.id).catch(() => undefined);
}
function onPick(e: Event) {
  const input = e.target as HTMLInputElement;
  if (input.files) void add([...input.files]);
  input.value = '';
}
function onDrop(e: DragEvent) {
  dragging.value = false;
  if (e.dataTransfer?.files.length) void add([...e.dataTransfer.files]);
}
function onPaste(e: ClipboardEvent) {
  const files = [...(e.clipboardData?.files ?? [])];
  if (!files.length) return;
  e.preventDefault();
  void add(files);
}

async function send() {
  if (slashFirst()) return;
  const text = draft.value.trim();
  if ((!text && !attached.value.length) || chat.busy || uploading.value) return;
  if (!chat.model) {
    snackbar.error(t('playground.noModel'));
    return;
  }
  const keep = { draft: draft.value, pending: pending.value };
  const attachments = attached.value;
  draft.value = '';
  pending.value = [];
  pinned = true;
  try {
    await chat.submit(text, attachments);
  } catch (error) {
    draft.value = keep.draft;
    pending.value = keep.pending;
    snackbar.error(error instanceof Error && error.message ? error.message : t('playground.sendFailed'));
  }
}

// -- editing, retrying, copying, branching -----------------------------------------
const editingId = ref<string | null>(null);
/** The up arrow in an empty composer: the last question, to edit. */
function recall() {
  const last = [...chat.path].reverse().find((m) => m.role === 'user' && !isLocal(m.id));
  if (last && !chat.busy) editingId.value = last.id;
}
async function submitEdit(m: MessageView, content: string, attachments: AttachmentView[]) {
  editingId.value = null;
  pinned = true;
  try {
    await chat.edit(m.id, content, attachments);
  } catch (error) {
    snackbar.error(error instanceof Error && error.message ? error.message : t('playground.sendFailed'));
  }
}
/** Another reply, from the model and level now in the composer: switch them first to try another. */
async function retry(m: MessageView) {
  pinned = true;
  try {
    await chat.retry(m.id, chat.model ?? undefined, chat.effort);
  } catch (error) {
    snackbar.error(error instanceof Error && error.message ? error.message : t('playground.failed'));
  }
}
async function branch(m: MessageView) {
  try {
    await chat.branch(m.id);
    snackbar.show(t('playground.branchOpened'));
  } catch (error) {
    snackbar.error(error instanceof Error ? error.message : t('common.error'));
  }
}
async function copy(text: string, done = t('common.copied')) {
  try {
    await navigator.clipboard.writeText(text);
    snackbar.show(done);
  } catch {
    snackbar.error(t('common.error'));
  }
}
/** The reply's request as a curl command against this gateway's public API, with the key left to the reader. */
async function copyRequest(m: MessageView) {
  const id = chat.conversation?.id;
  if (!id) return;
  try {
    const body = await conversationsApi.request(id, m.id);
    const url = new URL(`${BASE_URL}/v1/chat/completions`, window.location.href).href;
    const command = `curl ${url} \\\n  -H "Authorization: Bearer $HOSHIMI_API_KEY" \\\n  -H "Content-Type: application/json" \\\n  -d @- <<'JSON'\n${JSON.stringify(body, null, 2)}\nJSON`;
    await copy(command, t('playground.requestCopied'));
  } catch (error) {
    snackbar.error(error instanceof Error ? error.message : t('common.error'));
  }
}

// -- chats: new, temporary, open, rename, archive, delete ------------------------------
function newChat(options: { temporary?: boolean } = {}) {
  chat.startNew(options);
  editingId.value = null;
  drawerOpen.value = false;
  void nextTick(() => composer.value?.focus());
}
/**
 * The header's one button for what comes next. On a new chat no one has written in, it offers a
 * temporary chat instead; in a temporary chat, and in any chat with messages, it starts a new one.
 */
const offerTemporary = computed(() => !chat.temporary && !chat.path.length);
async function select(id: string) {
  drawerOpen.value = false;
  editingId.value = null;
  try {
    await chat.open(id);
  } catch (error) {
    snackbar.error(error instanceof Error ? error.message : t('common.error'));
  }
}
const chatMenu = computed(() => [
  { id: 'rename', label: t('playground.rename'), icon: icons.Pencil },
  { id: 'archive', label: t('playground.archive'), icon: icons.Archive },
  { id: 'delete', label: t('playground.delete'), icon: icons.Trash2, danger: true },
]);
const renaming = ref<ConversationSummary | null>(null);
const renameText = ref('');
const renameOpen = computed({ get: () => renaming.value !== null, set: (open) => !open && (renaming.value = null) });
const doomed = ref<ConversationSummary | null>(null);
const deleteOpen = computed({ get: () => doomed.value !== null, set: (open) => !open && (doomed.value = null) });
const deleting = ref(false);
const titleOf = (c: ConversationSummary) => c.title || t('playground.untitled');

async function act(action: ConversationAction, c: ConversationSummary) {
  if (action === 'rename') {
    renaming.value = c;
    renameText.value = c.title;
    return;
  }
  if (action === 'delete') {
    doomed.value = c;
    return;
  }
  try {
    await conversationsApi.update(c.id, { archived: action === 'archive' });
    snackbar.show(action === 'archive' ? t('playground.archivedOne') : t('playground.restoredOne'));
    if (action === 'archive' && c.id === chat.conversation?.id) newChat();
  } catch (error) {
    snackbar.error(error instanceof Error ? error.message : t('common.error'));
  }
  void refreshList();
}
async function saveRename() {
  const c = renaming.value;
  const title = renameText.value.trim();
  if (!c || !title) return;
  try {
    if (c.id === chat.conversation?.id) await chat.rename(title);
    else await conversationsApi.update(c.id, { title });
    renaming.value = null;
  } catch (error) {
    snackbar.error(error instanceof Error ? error.message : t('common.error'));
  }
  void refreshList();
}
async function remove() {
  const c = doomed.value;
  if (!c) return;
  deleting.value = true;
  try {
    await conversationsApi.remove(c.id);
    if (c.id === chat.conversation?.id) {
      chat.forget(c.id);
      newChat();
    }
    snackbar.show(t('playground.deletedOne'));
  } catch (error) {
    snackbar.error(error instanceof Error ? error.message : t('common.error'));
  } finally {
    deleting.value = false;
    doomed.value = null;
    void refreshList();
  }
}

// -- slash commands and the status ------------------------------------------------------
const slash = ref<InstanceType<typeof SlashMenu> | null>(null);
const slashContext = computed<SlashContext>(() => {
  const since = chat.path.slice(chat.path.map((m) => m.role).lastIndexOf('checkpoint') + 1);
  const kept = !!chat.conversation && chat.path.some((m) => !isLocal(m.id));
  return {
    busy: chat.busy,
    replied: kept && since.some((m) => m.role === 'assistant' && m.status !== 'streaming' && !isLocal(m.id)),
    kept,
    efforts: effortOptions.value.length > 1,
  };
});
const slashCurrent = computed(() => ({ model: chat.model, effort: chat.effort, permission: approval.value }));
const ZERO = { input: 0, cached: 0, output: 0, reasoning: 0 };
/** What ``/status`` shows: the server's numbers for a kept chat; for a new one, the model's window as the catalogue gives it. */
const statusView = computed<ContextView | null>(() => {
  if (chat.context && chat.context.model === chat.model) return { ...chat.context, effort: chat.effort, tools: toolsOn.value, approval: approval.value };
  if (chat.conversation && chat.path.length) return chat.context;
  const window = spec.value?.context_window || 131_072;
  const headroom = Math.min(spec.value?.max_output || 32_768, Math.max(4096, Math.floor(window / 10)), Math.floor(window / 2));
  const threshold = settings.data.value?.playground.compact_threshold ?? 0.85;
  return {
    model: chat.model ?? '', provider: spec.value?.provider ?? '', effort: chat.effort, window, window_source: spec.value?.context_window ? 'catalog' : 'default',
    limit: Math.min(Math.floor(window * threshold), window - headroom), headroom, used: 0, used_source: 'estimate',
    auto_compact: settings.data.value?.playground.auto_compact ?? true, last: ZERO, totals: ZERO, rounds: 0, replies: 0, compactions: 0, last_compaction: null,
    last_duration_ms: 0, total_duration_ms: 0, running_since: null, tools: toolsOn.value, approval: approval.value,
  };
});
const statusLabels = computed(() => ({
  effort: effortText.value ?? '',
  tools: toolsOn.value.map((g) => t(`playground.toolNames.${g}`)),
  approval: t(`settings.approvalModes.${approval.value}`),
}));
/** The chip a command changed glows once, so the eye finds what changed. */
const flashed = ref('');
let flashTimer: ReturnType<typeof setTimeout> | undefined;
function flash(what: string) {
  flashed.value = '';
  clearTimeout(flashTimer);
  void nextTick(() => {
    flashed.value = what;
    flashTimer = setTimeout(() => (flashed.value = ''), 800);
  });
}
function runSlash(id: SlashId, argument: string) {
  draft.value = '';
  if (id === 'new') newChat();
  else if (id === 'fork') void forkChat(argument);
  else if (id === 'compact') void compactChat(argument);
}
function chooseSlash(page: SlashPage, value: string) {
  draft.value = '';
  if (page === 'models') {
    chat.choose({ model: value, effort: '' });
    flash('model');
  } else if (page === 'efforts') {
    chat.choose({ effort: value });
    flash('effort');
  } else if (page === 'permissions') {
    chat.chooseApproval(value as ApprovalMode);
    flash('approval');
  }
}
const onSlashPage = (page: SlashPage) => page === 'status' && void chat.refreshContext();
/** The ring opens the status over the composer, and closes it again; what is in the box stays as it was. */
const openStatus = () => slash.value?.toggle('status');
/**
 * A draft that names a command, sent with Enter once its menu was closed: it runs (or its list opens
 * again) rather than going out as a message. Anything else (a path, a word after a slash) is a message.
 */
function slashFirst(): boolean {
  const named = parseDraft(draft.value.trim());
  const command = named ? find(named.name) : undefined;
  if (!named || !command) return false;
  if (command.page) {
    if (!named.spaced) draft.value = `/${command.id} `;
    slash.value?.reopen();
    return true;
  }
  const reason = unavailable(command.id, slashContext.value);
  if (reason) snackbar.show(t(reason));
  else runSlash(command.id, named.rest);
  return true;
}
async function forkChat(title: string) {
  const leaf = chat.leaf;
  if (!chat.conversation || !leaf || isLocal(leaf)) return;
  try {
    await chat.branch(leaf);
    if (title.trim()) await chat.rename(title.trim());
    snackbar.show(t('playground.slash.forked'));
  } catch (error) {
    snackbar.error(error instanceof Error ? error.message : t('common.error'));
  }
}
async function compactChat(instructions: string) {
  pinned = true;
  try {
    if (await chat.compact(instructions)) snackbar.show(t('playground.slash.compacted'));
  } catch (error) {
    snackbar.error(t('playground.slash.compactFailed', { reason: error instanceof Error ? error.message : '' }));
  }
}
const checkpointStep = (m: MessageView) => (m.steps?.[0] ?? { type: 'compaction', id: m.id, status: 'running', trigger: 'manual', phase: 'manual', round: 0, summary: m.content, recent: [], tokens_before: 0, tokens_after: 0, model: m.model, ms: 0, error: m.error, at: m.created_at }) as CompactionStep;

// -- coming back, and leaving ---------------------------------------------------------
// The chat open last comes back when the page first opens, unless it was opened to try one model.
// While it is on its way the thread stays blank rather than greeting a new chat it is not, and it
// fades in once it comes (or the greeting does, when there was none).
const restoring = ref(!openedForModel && !chat.conversation);
onMounted(async () => {
  if (!restoring.value) return;
  try {
    const { conversation_id } = await conversationsApi.session();
    if (conversation_id && !chat.conversation && !chat.path.length) await chat.open(conversation_id);
  } catch {
    /* nothing to come back to */
  } finally {
    restoring.value = false;
  }
});
function onKey(e: KeyboardEvent) {
  if ((e.ctrlKey || e.metaKey) && e.shiftKey && e.key.toLowerCase() === 'o') {
    e.preventDefault();
    newChat();
  }
}
onMounted(() => window.addEventListener('keydown', onKey));
onActivated(() => window.addEventListener('keydown', onKey));
onDeactivated(() => window.removeEventListener('keydown', onKey));
onBeforeUnmount(() => {
  window.removeEventListener('keydown', onKey);
  chat.stop();
});
</script>

<template>
  <div ref="root" class="playground" :class="{ empty, wide }">
    <Transition name="chat-sidebar" :css="laidOut">
      <aside v-if="wide && sidebarOpen" class="sidebar">
        <ChatSidebar :current="chat.conversation?.id ?? null" @select="select" @act="act" />
      </aside>
    </Transition>

    <div ref="main" class="main" @click="viewer.onClick" @dragenter.prevent="dragging = true" @dragover.prevent @dragleave.self="dragging = false" @drop.prevent="onDrop">
      <header class="chat-header">
        <button
          type="button"
          class="list-toggle state-layer"
          :aria-expanded="listOpen"
          :aria-label="listOpen ? t('playground.closeSidebar') : t('playground.openSidebar')"
          :title="listOpen ? t('playground.closeSidebar') : t('playground.openSidebar')"
          @click="toggleList"
        >
          <AppIcon :icon="icons.PanelLeft" :size="24" />
        </button>
        <span class="spacer" />
        <AppMenu v-if="chat.conversation && !chat.conversation.temporary" :items="chatMenu" @select="(a) => act(a as ConversationAction, chat.conversation!)">
          <template #default="{ toggle }"><IconButton :icon="icons.MoreHorizontal" :label="t('playground.chatMenu')" @click="toggle" /></template>
        </AppMenu>
        <Transition :name="TRANSITIONS.chip" mode="out-in">
          <button v-if="offerTemporary" key="temporary" type="button" class="corner state-layer type-label-large" :title="t('playground.temporaryChat')" @click="newChat({ temporary: true })">
            <AppIcon :icon="icons.MessageCircleDashed" :size="20" />
            <span class="label">{{ t('playground.temporaryChat') }}</span>
          </button>
          <button v-else key="new" type="button" class="corner state-layer type-label-large" :title="t('playground.newChat')" @click="newChat()">
            <AppIcon :icon="icons.SquarePen" :size="20" />
            <span class="label">{{ t('playground.newChat') }}</span>
          </button>
        </Transition>
      </header>
      <CatalogStatus />

      <div ref="scroller" class="thread app-scroll" role="log" @scroll.passive="onScroll">
        <div ref="column" class="column">
          <p v-if="chat.temporary" class="temporary-note type-body-medium" role="note">
            <AppIcon :icon="icons.MessageCircleDashed" :size="18" />{{ t('playground.temporaryText') }}
          </p>
          <!-- One conversation replaced by another, or by the greeting, fades through. -->
          <FadeSwap>
            <div v-if="!empty" :key="chat.conversation?.id ?? ''" class="conversation">
              <VersionPanes v-slot="{ message: m, index: i }" :path="chat.path" :siblings="chat.siblings">
                <UserMessage
                  v-if="m.role === 'user'"
                  :message="m"
                  v-bind="versionsOf(m)"
                  :editing="editingId === m.id"
                  :busy="chat.busy"
                  :local="isLocal(m.id)"
                  @copy="copy(m.content)"
                  @edit-start="editingId = m.id"
                  @edit-cancel="editingId = null"
                  @edit-submit="(content, attachments) => submitEdit(m, content, attachments)"
                  @previous="step(m, -1)"
                  @next="step(m, 1)"
                />
                <CompactionMarker v-else-if="m.role === 'checkpoint'" :step="checkpointStep(m)" :settled="m.status !== 'streaming'" />
                <AssistantMessage
                  v-else
                  :message="m"
                  :started-at="m.status === 'streaming' ? chat.startedAt : 0"
                  v-bind="versionsOf(m)"
                  :busy="chat.busy"
                  :last="i === chat.path.length - 1"
                  :stage="chat.stage"
                  :local="isLocal(m.id)"
                  :temporary="chat.temporary"
                  @copy="copy(m.content)"
                  @copied="snackbar.show(t('common.copied'))"
                  @copy-failed="snackbar.error(t('common.error'))"
                  @retry="retry(m)"
                  @branch="branch(m)"
                  @copy-request="copyRequest(m)"
                  @previous="step(m, -1)"
                  @next="step(m, 1)"
                  @approve="approve"
                />
              </VersionPanes>
              <div v-for="q in chat.queued" :key="q.id" class="queued" role="status">
                <p class="bubble type-body-large">{{ q.text }}</p>
                <span class="type-body-small muted">{{ t('playground.steerQueued') }}</span>
              </div>
            </div>
            <EmptyState
              v-else-if="!restoring"
              key="greeting"
              class="greeting"
              :icon="icons.Sparkles"
              :title="models.data.value?.data.length || models.isFetching.value ? t('playground.empty') : t('playground.noModels')"
              :text="models.data.value?.data.length || models.isFetching.value ? '' : t('playground.noModelsText')"
            />
          </FadeSwap>
        </div>
      </div>

      <div class="dock" @paste="onPaste">
        <p v-if="visionHint" class="vision-hint type-body-small muted"><AppIcon :icon="icons.ImageOff" :size="18" />{{ t('playground.visionHint') }}</p>
        <Composer
          ref="composer"
          v-model="draft"
          :label="t('playground.message')"
          :placeholder="steerable ? t('playground.placeholderSteer') : chat.temporary ? t('playground.placeholderTemporary') : t('playground.placeholder')"
          :send-label="t('playground.send')"
          :stop-label="t('playground.stop')"
          :steer-label="t('playground.steer')"
          :busy="chat.busy"
          :steerable="steerable"
          :can-send="!!chat.model && !uploading"
          :attached="attached.length > 0"
          :keydown-guard="(e: KeyboardEvent) => slash?.onKey(e) ?? false"
          :combobox="slash?.combobox ?? null"
          @send="send"
          @steer="steerNow"
          @stop="chat.stop()"
          @recall="recall"
        >
          <template #above>
            <ul v-if="runningTerminals.length" class="terminals" :aria-label="t('playground.terminals')">
              <li v-for="term in runningTerminals" :key="term.id" class="terminal type-body-small">
                <AppIcon :icon="icons.SquareTerminal" :size="18" class="muted" />
                <code class="command" :title="term.command">{{ term.command }}</code>
                <span class="muted">{{ t('playground.terminalRunning') }}</span>
                <IconButton :icon="icons.CircleStop" :label="t('playground.stopTerminal', { command: term.command })" @click="chat.stopTerminal(term.id)" />
              </li>
            </ul>
            <div v-if="pending.length" class="tray">
              <AttachmentPreview
                v-for="p in pending"
                :key="p.key"
                :kind="p.kind"
                :name="p.name"
                :bytes="p.bytes"
                :url="p.url"
                :mime="p.view?.mime"
                :uploading="!p.view"
                removable
                @remove="drop(p.key)"
              />
            </div>
          </template>
          <template #tools>
            <ChipButton class="attach" :icon="icons.Plus" :label="t('playground.attach')" @click="fileInput?.click()" />
            <input ref="fileInput" class="file-input" type="file" multiple tabindex="-1" aria-hidden="true" @change="onPick" />
            <ComposerMenu
              class="model"
              :class="{ flash: flashed === 'model' }"
              :align="phone ? 'center' : 'start'"
              :model-value="chat.model"
              :label="t('playground.model')"
              :options="modelOptions"
              :placeholder="t('playground.model')"
              :disabled="chat.busy || !modelOptions.length"
              :loading="models.isFetching.value && !modelOptions.length"
              :search-label="t('models.search')"
              :search-from="8"
              :no-matches="t('models.none')"
              @update:model-value="(m) => chat.choose({ model: m, effort: '' })"
            />
            <Transition :name="TRANSITIONS.chipRow" v-bind="chipRowHooks">
              <span v-if="effortOptions.length > 1" ref="effortSlot" class="chip-slot">
                <ComposerMenu
                  :model-value="chat.effort"
                  class="effort"
                  :class="{ flash: flashed === 'effort' }"
                  :icon="icons.Brain"
                  :label="t('playground.effort')"
                  :hint="t('playground.effortHelp')"
                  :text="effortText"
                  :options="effortOptions"
                  :disabled="chat.busy"
                  @update:model-value="(e) => chat.choose({ effort: e ?? '' })"
                />
              </span>
            </Transition>
            <span ref="approvalSlot" class="chip-slot">
              <ComposerMenu
                class="approval-menu"
                :class="{ flash: flashed === 'approval' }"
                :model-value="approval"
                :icon="APPROVAL_ICONS[approval]"
                :label="t('settings.approval')"
                :hint="t('playground.approvalHint', { mode: t(`settings.approvalModes.${defaultApproval}`) })"
                :text="t(`playground.approvalShort.${approval}`)"
                :options="approvalOptions"
                :danger="approval === 'full'"
                @update:model-value="(m) => m && chat.chooseApproval(m as ApprovalMode)"
              />
            </span>
          </template>
          <template #actions>
            <Transition :name="TRANSITIONS.chipRow" v-bind="chipRowHooks">
              <span v-if="chat.conversation && statusView && statusView.used > 0" class="chip-slot">
                <ContextRing
                  :used="statusView.used"
                  :window="statusView.window"
                  :limit="statusView.limit"
                  :estimate="statusView.used_source === 'estimate'"
                  @click="openStatus"
                />
              </span>
            </Transition>
          </template>
        </Composer>
        <SlashMenu
          ref="slash"
          v-model:draft="draft"
          :anchor="() => composer?.surface() ?? null"
          :context="slashContext"
          :models="modelOptions"
          :efforts="effortOptions"
          :permissions="approvalOptions"
          :current="slashCurrent"
          :status="statusView"
          :status-labels="statusLabels"
          :conversation-id="chat.conversation?.id ?? null"
          @terminals-changed="chat.refreshTerminals()"
          @run="runSlash"
          @choose="chooseSlash"
          @page="onSlashPage"
        />
        <p class="hint type-body-small muted">{{ t('playground.hint') }}</p>
      </div>
      <!-- Takes the room under the composer while there is no conversation, so the composer sits in the middle. -->
      <div class="below settle" aria-hidden="true" />
      <div v-if="dragging" class="drop type-title-medium" aria-hidden="true"><AppIcon :icon="icons.Paperclip" :size="24" />{{ t('playground.dropHere') }}</div>
    </div>

    <SideSheet v-if="!wide" v-model:open="drawerOpen" side="start" :title="t('playground.chats')" :close-label="t('common.close')">
      <ChatSidebar :current="chat.conversation?.id ?? null" @select="select" @act="act" />
    </SideSheet>
    <ImageViewer v-model:open="viewer.open.value" v-model:index="viewer.index.value" :images="viewer.images.value" :from-rect="viewer.fromRect.value" :labels="viewerLabels" />
    <AppDialog v-model:open="renameOpen" :title="t('playground.renameTitle')" width="small">
      <TextField v-model="renameText" :label="t('playground.titleLabel')" @enter="saveRename" />
      <template #actions>
        <AppButton variant="text" @click="renaming = null">{{ t('common.cancel') }}</AppButton>
        <AppButton variant="filled" :disabled="!renameText.trim()" @click="saveRename">{{ t('playground.save') }}</AppButton>
      </template>
    </AppDialog>
    <ConfirmDialog
      v-model:open="deleteOpen"
      :title="t('playground.deleteTitle')"
      :message="doomed ? t('playground.deleteText', { title: titleOf(doomed) }) : ''"
      :confirm-label="t('playground.delete')"
      :cancel-label="t('common.cancel')"
      :loading="deleting"
      danger
      @confirm="remove"
    />
  </div>
</template>

<style scoped>
.playground {
  /* The page fills the frame (meta.fill), so the shell keeps no lane beside it: both sides pad alike. */
  --column: 768px;
  --pad: var(--app-space-6);
  display: flex; gap: var(--app-space-4); height: 100%; padding: var(--app-space-3) var(--pad) var(--app-space-3);
}
/* Where there is room, the chats stand beside the open one and scroll on their own. */
.sidebar {
  --width: clamp(240px, 22%, 300px);
  display: flex; flex-direction: column; flex: none; width: var(--width); min-height: 0;
  padding-inline-end: var(--app-space-3); border-inline-end: 1px solid var(--md-sys-color-outline-variant);
}
/* It slides out from the start, and the chat makes room as it comes (its width and the gap after it
   are taken back by the margin); it goes the same way. */
.chat-sidebar-enter-active, .chat-sidebar-leave-active {
  transition:
    margin-inline-start var(--md-sys-motion-duration-medium2) var(--md-sys-motion-easing-emphasized),
    transform var(--md-sys-motion-duration-medium2) var(--md-sys-motion-easing-emphasized),
    opacity var(--md-sys-motion-duration-short4) var(--md-sys-motion-easing-standard);
}
.chat-sidebar-leave-active { transition-duration: var(--md-sys-motion-duration-short4); }
.chat-sidebar-enter-from, .chat-sidebar-leave-to { margin-inline-start: calc(-1 * (var(--width) + var(--app-space-4))); transform: translateX(-24px); opacity: 0; }
.main { position: relative; display: flex; flex-direction: column; flex: 1; min-width: 0; min-height: 0; }
.chat-header { display: flex; align-items: center; gap: var(--app-space-1); flex: none; min-width: 0; padding-bottom: var(--app-space-2); }
.spacer { flex: 1; }
.list-toggle {
  display: grid; place-items: center; width: 40px; height: 40px; padding: 0; border: 0; border-radius: var(--md-sys-shape-corner-full);
  background: transparent; color: var(--md-sys-color-on-surface-variant); cursor: pointer;
}
/* The one button for what comes next, in the header's end corner. */
.corner {
  display: inline-flex; align-items: center; gap: var(--app-space-2); height: 36px; padding: 0 var(--app-space-3); border: 0;
  border-radius: var(--md-sys-shape-corner-full); background: transparent; color: var(--md-sys-color-on-surface-variant); cursor: pointer;
}
/* A long model id gives way first in the composer's row: the level is one short word. */
.model { min-width: 0; max-width: 320px; }
/* The thread reaches into the page's padding by its scrollbar's lane, so its column lines up with the
   composer's (never past the page's edge, where the page would scroll sideways). */
.thread {
  flex: 1 1 auto; min-height: 0; display: flex; flex-direction: column;
  margin-inline-end: calc(-1 * min(var(--app-scroll-lane), var(--pad)));
}
.column { display: flex; flex-direction: column; gap: var(--app-space-4); width: min(100%, var(--column)); margin: 0 auto; padding: var(--app-space-2) 0 var(--app-space-6); }
/* The greeting sits on the composer, the two centred together as one block. */
.empty .column { flex: 1; justify-content: flex-end; padding-bottom: var(--app-space-4); }
/* A long conversation fades in from its foot, where it opens: the part in view does not drift. */
.conversation { display: flex; flex-direction: column; gap: var(--app-space-4); min-width: 0; transform-origin: 50% 100%; }
.temporary-note {
  display: flex; align-items: center; gap: var(--app-space-2); margin: 0; padding: var(--app-space-2) var(--app-space-3);
  border-radius: var(--md-sys-shape-corner-medium); background: var(--md-sys-color-tertiary-container); color: var(--md-sys-color-on-tertiary-container);
}
.dock { display: flex; flex-direction: column; gap: var(--app-space-2); flex: none; width: min(100%, var(--column)); align-self: center; }
.tray { display: flex; flex-wrap: wrap; gap: var(--app-space-2); padding: var(--app-space-3) var(--app-space-4) 0; }
.file-input { display: none; }
.attach, .effort, .approval-menu { flex: none; }
/* A chip's own box, which its width opens and eases in (chip-row, useMorphWidth): no padding of its own. */
.chip-slot { display: inline-flex; flex: none; min-width: 0; }
/* A chip a slash command just changed glows once. */
.flash :deep(.face) { animation: chip-flash 0.8s var(--md-sys-motion-easing-standard); }
@keyframes chip-flash {
  0% { background: transparent; }
  25% { background: var(--md-sys-color-primary-container); color: var(--md-sys-color-on-primary-container); transform: scale(1.04); }
  100% { background: transparent; }
}
@media (prefers-reduced-motion: reduce) { .flash :deep(.face) { animation: none; } }
/* A message sent to the reply under way, waiting for its next round: a question's bubble, faded. */
.queued { display: flex; flex-direction: column; align-items: flex-end; gap: 2px; }
.queued .bubble {
  max-width: min(85%, 560px); margin: 0; padding: var(--app-space-2) var(--app-space-4); border-radius: 20px;
  background: var(--md-sys-color-surface-container-high); white-space: pre-wrap; overflow-wrap: anywhere; opacity: 0.6;
}
/* On a phone the shield says how tools ask by its look alone (red for full access): the model's name needs the room. */
@media (max-width: 599px) {
  .approval-menu :deep(.text), .approval-menu :deep(.chevron) { display: none; }
  .approval-menu :deep(.face) { justify-content: center; width: 32px; padding: 0; }
}
.terminals { display: flex; flex-direction: column; gap: 2px; margin: 0; padding: var(--app-space-2) var(--app-space-3) 0 var(--app-space-4); list-style: none; }
.terminal { display: flex; align-items: center; gap: var(--app-space-2); min-width: 0; }
.terminal .command { flex: 1; min-width: 0; overflow: hidden; white-space: nowrap; text-overflow: ellipsis; font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }
.vision-hint { display: flex; align-items: center; gap: var(--app-space-1); margin: 0; padding: 0 var(--app-space-2); }
.hint { margin: 0; text-align: center; }
.below { flex: 0 0 0; }
.empty .below { flex-grow: 1; }
.drop {
  position: absolute; inset: 0; z-index: var(--app-z-dropdown); display: flex; align-items: center; justify-content: center; gap: var(--app-space-2);
  border: 2px dashed var(--md-sys-color-primary); border-radius: var(--md-sys-shape-corner-large);
  background: color-mix(in srgb, var(--md-sys-color-primary-container) 70%, transparent); color: var(--md-sys-color-on-primary-container); pointer-events: none;
}
@media (max-width: 599px) {
  .playground { --pad: var(--app-space-3); padding: var(--app-space-2) var(--pad); }
  .hint { display: none; }
  .corner .label { display: none; }
  .corner { width: 40px; padding: 0; justify-content: center; }
}
</style>
