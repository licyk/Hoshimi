<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import { ApiError } from '@/api/client';
import { streamChat, type ChatMessage } from '@/api/chat';
import { conversationsApi, useRefreshConversations } from '@/api/conversations';
import { useModelSpecs, useModels } from '@/api/queries/app';
import { useI18n } from '@/i18n';
import CatalogStatus from '@/components/CatalogStatus.vue';
import ConversationList from '@/components/ConversationList.vue';
import { ChipButton, Composer, ComposerMenu, EmptyState, SideSheet, TRANSITIONS, icons, keepLeavingInPlace, useSnackbar } from '@/ui';
import { useElementWidth } from '@/ui/useElementWidth';
// Not through '@/ui': markdown-it and highlight.js would then load with every page, not with this one.
import Markdown from '@/ui/Markdown.vue';

const { t } = useI18n();
const route = useRoute();
const router = useRouter();
const models = useModels();
const snackbar = useSnackbar();

interface Turn {
  role: 'user' | 'assistant';
  content: string;
}
const turns = ref<Turn[]>([]);

const specs = useModelSpecs();
const model = ref<string | null>(null);
// The models page opens the Playground to try one model: ?model=<id>, here or already kept alive.
// It is an instruction, not a state: another model than the one in use starts a new conversation
// rather than switch this one's, and the address lets it go, so a reload returns to the session.
const openedForModel = typeof route.query.model === 'string' && !!route.query.model;
watch(
  () => route.query.model,
  (value) => {
    if (typeof value !== 'string' || !value) return;
    if (value !== model.value) {
      if (turns.value.length) newChat();
      model.value = value;
    }
    void router.replace({ query: {} });
  },
  { immediate: true },
);
watch(
  () => models.data.value?.data,
  (data) => {
    if (!model.value && data?.length) model.value = data[0].id;
  },
  { immediate: true },
);
// A catalogue runs to hundreds of ids: the picker searches them, and names a model beside its id.
const modelOptions = computed(() => (models.data.value?.data ?? []).map((m) => ({ value: m.id, label: m.id, description: [m.display_name !== m.id ? m.display_name : '', m.owned_by].filter(Boolean).join(' · '), keywords: [m.owned_by, m.display_name ?? ''] })));
/** The model in front of the picker: its effort menu is that model's, not the lane's. */
const spec = computed(() => specs.data.value?.data.find((m) => m.id === model.value));
/** `''` sends no level at all — the gateway then leaves the body exactly as it was. */
const effort = ref('');
const effortOptions = computed(() => [
  { value: '', label: t('playground.effortAuto') },
  ...(spec.value?.efforts ?? []).map((o) => ({ value: o.id, label: o.name })),
]);
watch(model, () => (effort.value = ''));
/** The control shows the level's short name; the menu says what Auto means. */
const effortText = computed(() => (effort.value ? effortOptions.value.find((o) => o.value === effort.value)?.label : t('playground.effortAutoShort')));
const setModel = (value: string | null) => (model.value = value);

const draft = ref('');
const streaming = ref(false);
const scroller = ref<HTMLElement | null>(null);
const composer = ref<InstanceType<typeof Composer> | null>(null);
/** No conversation yet: the composer waits in the middle of the page, under the greeting. */
const empty = computed(() => !turns.value.length);
let controller: AbortController | null = null;

const toMessage = (turn: Turn): ChatMessage => ({ role: turn.role, content: turn.content });

async function scrollDown() {
  await nextTick();
  scroller.value?.scrollTo({ top: scroller.value.scrollHeight });
}

// -- the kept conversation ----------------------------------------------------
/** The conversation on screen, as the server keeps it; null until the first message is sent. */
const conversationId = ref<string | null>(null);
const refreshList = useRefreshConversations();
/**
 * Writes to the server wait for each other, so a reply is never filed before the question it
 * answers. A failed one is said once and does not stop the conversation, which goes on unkept.
 */
let saving: Promise<unknown> = Promise.resolve();
function save(work: () => Promise<unknown>) {
  saving = saving.then(work).catch(() => snackbar.error(t('playground.saveFailed')));
  return saving;
}
/** The id to file this send under, creating the conversation on its first message. */
async function conversationForSend(): Promise<string | null> {
  if (conversationId.value) return conversationId.value;
  try {
    const created = await conversationsApi.create(model.value ?? '', effort.value);
    conversationId.value = created.id;
    void save(() => conversationsApi.setSession(created.id));
    return created.id;
  } catch {
    snackbar.error(t('playground.saveFailed'));
    return null;
  }
}
// The model and level are the conversation's own: picking others is kept with it. Not while a
// conversation is being opened, whose own values are being put in place.
let opening = false;
watch([model, effort], ([m, e]) => {
  const id = conversationId.value;
  if (!opening && id) void save(() => conversationsApi.update(id, { model: m ?? '', effort: e }));
});

async function send() {
  const text = draft.value.trim();
  if (!text || streaming.value) return;
  if (!model.value) {
    snackbar.error(t('playground.noModel'));
    return;
  }
  draft.value = '';
  turns.value.push({ role: 'user', content: text });
  // The history the model answers: everything sent so far, without the placeholder reply.
  const history: ChatMessage[] = turns.value.map(toMessage);
  const reply = reactive<Turn>({ role: 'assistant', content: '' });
  turns.value.push(reply);
  streaming.value = true;
  controller = new AbortController();
  const sentWith = model.value;
  void scrollDown();
  // Kept under the conversation this send began in, even if another is opened before it ends.
  const id = await conversationForSend();
  if (id) void save(() => conversationsApi.append(id, 'user', text));
  try {
    await streamChat(
      sentWith,
      history,
      (delta) => {
        reply.content += delta;
        void scrollDown();
      },
      controller.signal,
      effort.value ? { reasoning_effort: effort.value } : {},
    );
  } catch (error) {
    if ((error as Error).name !== 'AbortError') {
      snackbar.error(error instanceof ApiError ? error.message : t('playground.failed'));
    }
  } finally {
    // Keep partial output on cancellation or failure, but never replay an empty placeholder.
    if (!reply.content) {
      const at = turns.value.indexOf(reply);
      if (at >= 0) turns.value.splice(at, 1);
    } else if (id) {
      void save(() => conversationsApi.append(id, 'assistant', reply.content, sentWith));
    }
    void saving.then(() => refreshList());
    streaming.value = false;
    controller = null;
  }
}

function stop() {
  controller?.abort();
}
onBeforeUnmount(stop);

const historyOpen = ref(false);
/** A fresh conversation with the model and level in use; the one on screen stays in the history. */
function newChat() {
  stop();
  conversationId.value = null;
  turns.value = [];
  draft.value = '';
  historyOpen.value = false;
  void save(() => conversationsApi.setSession(null));
  void nextTick(() => composer.value?.focus());
}

/** Puts a kept conversation on screen, with the model and level it was last used with. */
async function open(id: string, { remember = true } = {}) {
  historyOpen.value = false;
  if (id === conversationId.value) return;
  stop();
  let conversation;
  try {
    conversation = await conversationsApi.get(id);
  } catch (error) {
    snackbar.error(error instanceof Error ? error.message : t('common.error'));
    return;
  }
  opening = true;
  conversationId.value = conversation.id;
  turns.value = conversation.messages.map((m) => ({ role: m.role, content: m.content }));
  draft.value = '';
  if (conversation.model) model.value = conversation.model;
  // After the model's own watcher, which resets the level to Auto.
  await nextTick();
  effort.value = conversation.effort;
  await nextTick();
  opening = false;
  if (remember) void save(() => conversationsApi.setSession(conversation.id));
  void scrollDown();
}

const onDeleted = (id: string) => id === conversationId.value && newChat();

// The conversation open last comes back when the page first opens, unless the page was opened to
// try one model (?model=), which starts afresh.
onMounted(async () => {
  if (openedForModel) return;
  try {
    const { conversation_id } = await conversationsApi.session();
    if (conversation_id && !turns.value.length && !conversationId.value) await open(conversation_id, { remember: false });
  } catch {
    /* nothing to come back to */
  }
});

// The history sits beside the conversation where there is room, and in a side sheet where there is not.
const root = ref<HTMLElement | null>(null);
const width = useElementWidth(root, 1280);
const wide = computed(() => width.value >= 960);
</script>

<template>
  <div ref="root" class="playground" :class="{ empty, wide }">
    <aside v-if="wide" class="history-pane" :aria-label="t('playground.history')">
      <ConversationList :current="conversationId" @select="open" @new="newChat" @deleted="onDeleted" />
    </aside>
    <div class="chat">
      <CatalogStatus />

      <div ref="scroller" class="thread app-scroll" role="log" aria-live="polite">
        <Transition :name="TRANSITIONS.fadeThrough" mode="out-in">
          <EmptyState
            v-if="empty"
            key="greeting"
            class="greeting"
            :icon="icons.Sparkles"
            :title="models.data.value?.data.length || models.isFetching.value ? t('playground.empty') : t('playground.noModels')"
            :text="models.data.value?.data.length || models.isFetching.value ? t('playground.emptyText') : t('playground.noModelsText')"
          />
          <TransitionGroup v-else key="turns" :name="TRANSITIONS.list" tag="div" class="turns">
            <div v-for="(turn, i) in turns" :key="i" class="turn" :class="turn.role">
              <span class="who type-label-medium">{{ turn.role === 'user' ? t('playground.you') : t('playground.assistant') }}</span>
              <!-- A reply is Markdown, rendered as it streams; what the user typed stays the text they typed. -->
              <div class="bubble type-body-medium" :class="{ rich: turn.role === 'assistant' && turn.content }">
                <Markdown
                  v-if="turn.role === 'assistant' && turn.content"
                  :source="turn.content"
                  :copy-label="t('playground.copyCode')"
                  :copied-label="t('playground.codeCopied')"
                  @copied="snackbar.show(t('common.copied'))"
                  @copy-failed="snackbar.error(t('common.error'))"
                />
                <template v-else-if="turn.content">{{ turn.content }}</template>
                <span v-else class="muted">{{ t('playground.streaming') }}</span>
              </div>
            </div>
          </TransitionGroup>
        </Transition>
      </div>

      <div class="dock">
        <Composer
          ref="composer"
          v-model="draft"
          :label="t('playground.message')"
          :placeholder="t('playground.placeholder')"
          :send-label="t('playground.send')"
          :stop-label="t('playground.stop')"
          :busy="streaming"
          :can-send="!!model"
          @send="send"
          @stop="stop"
        >
          <template #tools>
            <TransitionGroup :name="TRANSITIONS.chip" tag="div" class="tools" v-bind="keepLeavingInPlace">
              <ComposerMenu
                key="model"
                class="model"
                :model-value="model"
                :label="t('playground.model')"
                :options="modelOptions"
                :placeholder="t('playground.model')"
                :disabled="streaming || !modelOptions.length"
                :loading="models.isFetching.value && !modelOptions.length"
                :search-label="t('models.search')"
                :search-from="8"
                :no-matches="t('models.none')"
                @update:model-value="setModel"
              />
              <ComposerMenu
                v-if="effortOptions.length > 1"
                key="effort"
                v-model="effort"
                class="effort"
                :icon="icons.Brain"
                :label="t('playground.effort')"
                :hint="t('playground.effortHelp')"
                :text="effortText"
                :options="effortOptions"
                :disabled="streaming"
              />
            </TransitionGroup>
          </template>
          <template #actions>
            <ChipButton v-if="!wide" :icon="icons.History" :label="t('playground.history')" @click="historyOpen = true" />
            <Transition :name="TRANSITIONS.chip" v-bind="keepLeavingInPlace">
              <ChipButton v-if="!empty" :icon="icons.MessageSquarePlus" :label="t('playground.newChat')" @click="newChat" />
            </Transition>
          </template>
        </Composer>
        <p class="hint type-body-small muted">{{ t('playground.hint') }}</p>
      </div>
      <!-- Takes the room under the composer while there is no conversation, so the composer sits in the middle. -->
      <div class="below settle" aria-hidden="true" />
    </div>
    <SideSheet v-if="!wide" v-model:open="historyOpen" :title="t('playground.history')" :close-label="t('common.close')">
      <ConversationList :current="conversationId" @select="open" @new="newChat" @deleted="onDeleted" />
    </SideSheet>
  </div>
</template>

<style scoped>
.playground {
  /* The conversation and the composer share one column, centred, with the room around it left empty. */
  /* The page fills the frame (meta.fill), so the shell keeps no lane beside it: both sides pad alike. */
  --column: 768px;
  --pad: var(--app-space-6);
  display: flex; gap: var(--app-space-4); height: 100%; padding: var(--app-space-4) var(--pad) var(--app-space-3);
}
/* Where there is room, the kept conversations stand beside the one on screen and scroll on their own. */
.history-pane {
  display: flex; flex-direction: column; flex: none; width: clamp(240px, 22%, 300px); min-height: 0;
  padding-inline-end: var(--app-space-4); border-inline-end: 1px solid var(--md-sys-color-outline-variant);
}
.history-pane > .conversation-list { flex: 1; }
.chat { display: flex; flex-direction: column; flex: 1; min-width: 0; min-height: 0; }
/* The thread reaches into the page's padding by its scrollbar's lane, so its column lines up with the
   composer's (never past the page's edge, where the page would scroll sideways). */
.thread {
  flex: 1 1 auto; min-height: 0; display: flex; flex-direction: column;
  margin-inline-end: calc(-1 * min(var(--app-scroll-lane), var(--pad)));
  padding: var(--app-space-2) max(0px, calc((100% - var(--column)) / 2)) var(--app-space-4);
}
/* The greeting keeps its own height and sits on the composer, the two centred together as one block. */
.thread > .greeting { flex: none; margin-top: auto; padding-bottom: var(--app-space-4); }
.turns { display: flex; flex-direction: column; gap: var(--app-space-3); }
.turn { display: flex; flex-direction: column; gap: var(--app-space-1); max-width: min(640px, 100%); }
.turn.assistant { align-self: flex-start; }
.turn.user { align-self: flex-end; align-items: flex-end; }
.who { min-width: 0; overflow-wrap: anywhere; }
.bubble { min-width: 0; padding: var(--app-space-2) var(--app-space-3); border-radius: var(--md-sys-shape-corner-large); white-space: pre-wrap; overflow-wrap: anywhere; }
/* Markdown lays out its own blocks; the source's line breaks are not the bubble's to keep. */
.bubble.rich { white-space: normal; }
.turn.user .bubble { background: var(--md-sys-color-primary-container); color: var(--md-sys-color-on-primary-container); }
.turn.assistant .bubble { background: var(--md-sys-color-surface-container); color: var(--md-sys-color-on-surface); }
.dock { display: flex; flex-direction: column; gap: var(--app-space-2); flex: none; width: min(100%, var(--column)); align-self: center; }
.tools { position: relative; display: flex; align-items: center; gap: 2px; min-width: 0; }
/* A long model id gives way first: the level is one short word. */
.model { max-width: 320px; }
.effort { flex: none; }
.hint { margin: 0; text-align: center; }
.below { flex: 0 0 0; }
.empty .below { flex-grow: 1; }
@media (max-width: 599px) {
  .playground { --pad: var(--app-space-3); padding: var(--app-space-3) var(--pad) var(--app-space-2); }
  .hint { display: none; }
}
</style>
