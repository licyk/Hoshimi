<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, reactive, ref, watch } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import { ApiError } from '@/api/client';
import { streamChat, type ChatMessage } from '@/api/chat';
import { useModelSpecs, useModels } from '@/api/queries/app';
import { useI18n } from '@/i18n';
import CatalogStatus from '@/components/CatalogStatus.vue';
import { AppButton, EmptyState, FieldRow, IconButton, SelectField, Skeleton, icons, useSnackbar } from '@/ui';

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
const modelOptions = computed(() => (models.data.value?.data ?? []).map((m) => ({ value: m.id, label: m.id })));
/** The model in front of the picker: its effort menu is that model's, not the lane's. */
const spec = computed(() => specs.data.value?.data.find((m) => m.id === model.value));
/** `''` sends no level at all — the gateway then leaves the body exactly as it was. */
const effort = ref('');
const effortOptions = computed(() => [
  { value: '', label: t('playground.effortAuto') },
  ...(spec.value?.efforts ?? []).map((o) => ({ value: o.id, label: o.name })),
]);
watch(model, () => (effort.value = ''));
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
}
function onKeydown(event: KeyboardEvent) {
  if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) {
    event.preventDefault();
    void send();
  }
}
</script>

<template>
  <div class="playground">
    <FieldRow>
      <div class="head-controls">
        <SelectField
          class="model"
          :model-value="model"
          :label="t('playground.model')"
          :options="modelOptions"
          :disabled="streaming || !modelOptions.length"
          @update:model-value="setModel"
        />
        <SelectField
          v-if="effortOptions.length > 1"
          v-model="effort"
          class="effort"
          :label="t('playground.effort')"
          :options="effortOptions"
          :disabled="streaming"
          :supporting-text="t('playground.effortHelp')"
        />
      </div>
      <template #end>
        <IconButton :icon="icons.Trash2" :label="t('playground.clear')" :disabled="!turns.length" @click="clear" />
      </template>
    </FieldRow>

    <CatalogStatus />

    <div ref="scroller" class="thread" role="log" aria-live="polite">
      <div v-if="models.isFetching.value && !modelOptions.length" class="loading">
        <Skeleton v-for="i in 3" :key="i" height="64px" shape="medium" />
      </div>
      <EmptyState
        v-else-if="!turns.length"
        :icon="icons.Sparkles"
        :title="models.data.value?.data.length ? t('playground.empty') : t('playground.noModels')"
        :text="models.data.value?.data.length ? t('playground.emptyText') : t('playground.noModelsText')"
      />
      <template v-else>
        <div v-for="(turn, i) in turns" :key="i" class="turn" :class="turn.role">
          <span class="who type-label-medium">{{ turn.role === 'user' ? t('playground.you') : t('playground.assistant') }}</span>
          <div class="bubble type-body-medium">
            <template v-if="turn.content">{{ turn.content }}</template>
            <span v-else class="muted">{{ t('playground.streaming') }}</span>
          </div>
        </div>
      </template>
    </div>

    <div class="composer">
      <textarea
        v-model="draft"
        class="input type-body-large"
        :placeholder="t('playground.placeholder')"
        :aria-label="t('playground.message')"
        rows="2"
        @keydown="onKeydown"
      />
      <div class="actions">
        <AppButton v-if="streaming" variant="outlined" :icon="icons.Square" @click="stop">{{ t('playground.stop') }}</AppButton>
        <AppButton v-else variant="filled" :icon="icons.Send" :disabled="!draft.trim() || !model" @click="send">{{ t('playground.send') }}</AppButton>
      </div>
    </div>
  </div>
</template>

<style scoped>
.playground { display: flex; flex-direction: column; gap: var(--app-space-3); width: 100%; height: 100%; padding: var(--app-space-4) var(--app-space-6) var(--app-space-4); }
.head-controls { display: flex; align-items: flex-start; gap: var(--app-space-2); min-width: 0; flex-wrap: wrap; }
.model { flex: 1 1 280px; min-width: 0; }
.effort { flex: 0 1 200px; min-width: 0; }
.thread { flex: 1; min-height: 0; overflow-y: auto; display: flex; flex-direction: column; gap: var(--app-space-3); padding: var(--app-space-2) 0; }
.loading { display: flex; flex-direction: column; gap: var(--app-space-2); }
.turn { display: flex; flex-direction: column; gap: var(--app-space-1); max-width: min(760px, 100%); }
.turn.assistant { align-self: flex-start; }
.turn.user { align-self: flex-end; align-items: flex-end; }
.who { min-width: 0; overflow-wrap: anywhere; }
.bubble { min-width: 0; padding: var(--app-space-2) var(--app-space-3); border-radius: var(--md-sys-shape-corner-medium); white-space: pre-wrap; overflow-wrap: anywhere; }
.turn.user .bubble { background: var(--md-sys-color-primary-container); color: var(--md-sys-color-on-primary-container); }
.turn.assistant .bubble { background: var(--md-sys-color-surface-container); color: var(--md-sys-color-on-surface); }
.composer { display: flex; align-items: flex-end; gap: var(--app-space-3); padding-top: var(--app-space-2); border-top: 1px solid var(--md-sys-color-outline-variant); }
.input {
  flex: 1;
  min-width: 0;
  min-height: 56px;
  max-height: 200px;
  padding: var(--app-space-3);
  border: 1px solid var(--md-sys-color-outline);
  border-radius: var(--md-sys-shape-corner-medium);
  background: var(--md-sys-color-surface-container-low);
  color: var(--md-sys-color-on-surface);
  font: inherit;
  resize: vertical;
}
.input:focus { outline: none; border-color: var(--md-sys-color-primary); }
.actions { display: flex; gap: var(--app-space-2); }
@media (max-width: 599px) {
  .playground { padding: var(--app-space-3); }
}
</style>
