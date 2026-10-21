<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue';
import { cancelLogin, pollLogin, startLogin, submitLoginInput, useLoginDone, type LoginSession } from '@/api/queries/channels';
import { useI18n } from '@/i18n';
import { AppButton, AppDialog, IconButton, Stepper, TextField, icons, useSnackbar, type StepItem } from '@/ui';

/**
 * Sign a channel in through the station's own page. The steps say where the login stands:
 * asking the station for a link, waiting while the operator finishes in the browser (with the
 * link and any device code to copy, and a field for a pasted code when the station asks for
 * one), and done. Opening the dialog starts a session; closing it, however it closes, cancels a
 * session still pending.
 */
const props = defineProps<{ provider: string | null; name: string }>();
const open = defineModel<boolean>('open', { default: false });
const { t } = useI18n();
const snackbar = useSnackbar();
const loginDone = useLoginDone();

const session = ref<LoginSession | null>(null);
const error = ref('');
const manualCode = ref('');
const submitting = ref(false);
let polling = false;
let poller: number | undefined;
let opened = false;
let generation = 0;

type Step = 'start' | 'browser' | 'done';
const step = computed<Step>(() => (!session.value ? 'start' : session.value.status === 'succeeded' ? 'done' : 'browser'));
const steps = computed<StepItem<Step>[]>(() => {
  const failed = !!error.value;
  const at = step.value;
  return [
    { id: 'start', label: t('channels.loginSteps.start'), state: at === 'start' ? (failed ? 'failed' : 'running') : 'done' },
    { id: 'browser', label: t('channels.loginSteps.browser'), state: at === 'browser' ? (failed ? 'failed' : 'running') : at === 'done' ? 'done' : 'pending' },
    { id: 'done', label: t('channels.loginSteps.done'), state: at === 'done' ? 'done' : 'pending' },
  ];
});
// The stepper shows where the login stands; it is not a way to jump between steps.
const shownStep = computed({ get: () => step.value, set: () => undefined });

async function begin(provider: string) {
  stop();
  const mine = ++generation;
  error.value = '';
  manualCode.value = '';
  session.value = null;
  opened = false;
  try {
    const started = await startLogin(provider);
    if (mine !== generation || !open.value) {
      if (started.status === 'pending') await cancelLogin(started.id);
      return;
    }
    session.value = started;
    poller = window.setInterval(poll, 1500);
    await poll();
  } catch (e) {
    if (mine === generation) error.value = (e as Error).message;
  }
}

async function poll() {
  const id = session.value?.id;
  if (!id || polling) return;
  polling = true;
  try {
    const view = await pollLogin(id);
    if (session.value?.id !== id || !open.value) return;
    // The URL only exists once the station issues one; open it the moment it does.
    if (view.login_url && !opened) {
      opened = true;
      window.open(view.login_url, '_blank', 'noopener');
    }
    session.value = view;
    if (view.status !== 'pending') {
      stopPolling();
      if (view.status === 'succeeded') {
        snackbar.show(t('channels.loginDone', { id: view.account_id }));
        loginDone();
      } else error.value = view.message || t('common.error');
    }
  } catch {
    /* one lost poll is not a verdict */
  } finally {
    polling = false;
  }
}

function stopPolling() {
  if (poller !== undefined) window.clearInterval(poller);
  poller = undefined;
}
function stop() {
  generation++;
  stopPolling();
  opened = false;
  if (session.value?.status === 'pending') void cancelLogin(session.value.id).catch((e: Error) => snackbar.error(e.message));
}
function reset() {
  stop();
  session.value = null;
  manualCode.value = '';
  error.value = '';
}

watch(open, (value) => (value && props.provider ? void begin(props.provider) : reset()));
onBeforeUnmount(reset);

async function sendCode() {
  if (!session.value || !manualCode.value.trim()) return;
  submitting.value = true;
  try {
    await submitLoginInput(session.value.id, manualCode.value);
    manualCode.value = '';
    error.value = '';
  } catch (e) {
    error.value = (e as Error).message;
  } finally {
    submitting.value = false;
  }
}

async function copy(text: string) {
  try {
    await navigator.clipboard.writeText(text);
    snackbar.show(t('common.copied'));
  } catch {
    snackbar.error(t('common.error'));
  }
}
const retry = () => props.provider && begin(props.provider);
</script>

<template>
  <AppDialog v-model:open="open" :title="t('channels.loginTitle', { name })" width="medium" @closed="reset">
    <div class="login">
      <Stepper v-model="shownStep" :steps="steps" />
      <p v-if="step === 'start' && !error" class="type-body-medium muted">{{ t('channels.loginStarting') }}</p>
      <template v-if="session && step === 'browser'">
        <p class="type-body-medium">{{ session.login_url ? t('channels.loginOpened') : t('channels.loginWaiting') }}</p>
        <div v-if="session.login_url" class="copy-row">
          <a :href="session.login_url" target="_blank" rel="noopener" class="type-body-small link mono">{{ session.login_url }}</a>
          <IconButton :icon="icons.Copy" :label="t('common.copy')" @click="copy(session.login_url)" />
        </div>
        <div v-if="session.user_code" class="code-box">
          <span class="type-label-large muted">{{ t('channels.deviceCode') }}</span>
          <div class="copy-row">
            <strong class="code mono">{{ session.user_code }}</strong>
            <IconButton :icon="icons.Copy" :label="t('common.copy')" @click="copy(session.user_code)" />
          </div>
          <span class="type-body-small muted">{{ t('channels.deviceInstructions') }}</span>
        </div>
        <form v-if="session.waiting_for === 'manual_code' && session.status === 'pending'" class="manual" @submit.prevent="sendCode">
          <TextField v-model="manualCode" type="password" autocomplete="off" :label="t('channels.manualCode')" :supporting-text="t('channels.manualCodeHelp')" @enter="sendCode" />
          <AppButton variant="tonal" :loading="submitting" :disabled="!manualCode.trim()" @click="sendCode">{{ t('channels.submitCode') }}</AppButton>
        </form>
        <p v-if="session.expires_at" class="type-body-small muted">{{ t('channels.loginExpires', { when: new Date(session.expires_at * 1000).toLocaleTimeString() }) }}</p>
      </template>
      <p v-if="step === 'done'" class="type-body-medium ok-text">{{ t('channels.loginDone', { id: session?.account_id ?? '' }) }}</p>
      <p v-if="error" class="type-body-medium error-text" role="alert">{{ error }}</p>
    </div>
    <template #actions>
      <AppButton v-if="error" variant="tonal" :icon="icons.RotateCcw" @click="retry">{{ t('common.retry') }}</AppButton>
      <AppButton :variant="step === 'done' ? 'filled' : 'text'" @click="open = false">{{ step === 'done' ? t('common.done') : t('common.cancel') }}</AppButton>
    </template>
  </AppDialog>
</template>

<style scoped>
.login { display: flex; flex-direction: column; gap: var(--app-space-3); }
p { margin: 0; }
.copy-row { display: flex; align-items: center; gap: var(--app-space-2); min-width: 0; }
.link { flex: 1; min-width: 0; color: var(--md-sys-color-primary); overflow-wrap: anywhere; }
.code-box { display: flex; flex-direction: column; gap: var(--app-space-1); padding: var(--app-space-3) var(--app-space-4); border-radius: var(--md-sys-shape-corner-medium); background: var(--md-sys-color-surface-container-high); }
.code { font-size: var(--md-sys-typescale-headline-small-size); letter-spacing: 0.08em; }
.manual { display: flex; flex-direction: column; gap: var(--app-space-2); align-items: flex-start; }
.manual > :first-child { align-self: stretch; }
.error-text { color: var(--md-sys-color-error); overflow-wrap: anywhere; }
.ok-text { color: var(--md-sys-color-primary); }
</style>
