<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, reactive, ref, watch } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import { ApiError } from '@/api/client';
import { streamChat, type ChatMessage } from '@/api/chat';
import { useModelSpecs, useModels } from '@/api/queries/app';
import { useI18n } from '@/i18n';
import CatalogStatus from '@/components/CatalogStatus.vue';
import { ChipButton, Composer, ComposerMenu, EmptyState, TRANSITIONS, icons, keepLeavingInPlace, useSnackbar } from '@/ui';

const { t } = useI18n();
const route = useRoute();
const router = useRouter();
const models = useModels();
const snackbar = useSnackbar();

const specs = useModelSpecs();
const model = ref<string | null>(null);
// The models page opens a conversation about one model: ?model=<id>, here or already kept alive.
watch(
  () => route.query.model,
  (value) => {
    if (typeof value === 'string' && value) model.value = value;
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
const setModel = (value: string | null) => {
  model.value = value;
  router.replace({ query: value ? { model: value } : {} });
};

interface Turn {
  role: 'user' | 'assistant';
  content: string;
}
const turns = ref<Turn[]>([]);
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
  void scrollDown();
  try {
    await streamChat(
      model.value,
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
    }
    streaming.value = false;
    controller = null;
  }
}

function stop() {
  controller?.abort();
}
onBeforeUnmount(stop);
function clear() {
  controller?.abort();
  turns.value = [];
  draft.value = '';
  composer.value?.focus();
}
</script>

<template>
  <div class="playground" :class="{ empty }">
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
            <div class="bubble type-body-medium">
              <template v-if="turn.content">{{ turn.content }}</template>
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
          <Transition :name="TRANSITIONS.chip" v-bind="keepLeavingInPlace">
            <ChipButton v-if="!empty" :icon="icons.Eraser" :label="t('playground.clear')" @click="clear" />
          </Transition>
        </template>
      </Composer>
      <p class="hint type-body-small muted">{{ t('playground.hint') }}</p>
    </div>
    <!-- Takes the room under the composer while there is no conversation, so the composer sits in the middle. -->
    <div class="below settle" aria-hidden="true" />
  </div>
</template>

<style scoped>
.playground {
  /* The conversation and the composer share one column, centred, with the room around it left empty. */
  --column: 768px;
  display: flex; flex-direction: column; height: 100%; padding: var(--app-space-4) var(--app-space-6) var(--app-space-3);
}
/* The thread reaches into the page's padding by its scrollbar's lane, so its column lines up with the composer's. */
.thread {
  flex: 1 1 auto; min-height: 0; display: flex; flex-direction: column;
  margin-inline-end: calc(-1 * var(--app-scrollbar-size));
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
  .playground { padding: var(--app-space-3) var(--app-space-3) var(--app-space-2); }
  .hint { display: none; }
}
</style>
