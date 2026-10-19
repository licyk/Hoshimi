<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue';
import { useProviders, useSettings, useUpdateSettings } from '@/api/queries/app';
import {
  pollLogin,
  cancelLogin,
  submitLoginInput,
  startLogin,
  useAddAccount,
  useBalances,
  useClaimCredits,
  useDeleteAccount,
  useLoginDone,
  useLoginProviders,
  useRefreshNow,
  useRefreshStatus,
  useStations,
  useProviderForms,
  type LoginSession,
  type PassiveQuota,
} from '@/api/queries/channels';
import { useI18n } from '@/i18n';
import { quotaBadge } from '@/format';
import { accountFromForm, AccountFormError } from '@/api/accountForms';
import { AppButton, AppDialog, Badge, ConfirmDialog, EmptyState, IconButton, SelectField, Skeleton, Surface, Switch, TextField, icons, useSnackbar } from '@/ui';

const { t } = useI18n();
const snackbar = useSnackbar();
const settings = useSettings();
const update = useUpdateSettings();
const providers = useProviders();
const stations = useStations();
const providerForms = useProviderForms();
const providerSearch = ref('');
const balances = useBalances();
const refresh = useRefreshStatus();
const loginStations = useLoginProviders();
const claim = useClaimCredits();
const refreshNow = useRefreshNow();
const remove = useDeleteAccount();
const add = useAddAccount();
const loginDone = useLoginDone();

/** The renewal state and the balance of one account, joined for the card that shows both. */
interface AccountRow {
  id: string;
  needsLogin: boolean;
  expiresAt: number | null;
  balance: number | null;
  unit: string;
  balanceNote: string;
}

interface Card {
  id: string;
  name: string;
  enabled: boolean;
  loginable: boolean;
  /** The backend publishes a credential form this page can build. */
  addable: boolean;
  accounts: AccountRow[];
  needsLogin: number;
  expired: number;
  /** What the lane reported about its own allowance, read off responses it was sending anyway. */
  quota: PassiveQuota | null;
}

const cards = computed<Card[]>(() => {
  const status = new Map((refresh.data.value?.accounts ?? []).map((a) => [`${a.provider}/${a.account}`, a]));
  const balance = new Map((balances.data.value?.rows ?? []).map((r) => [`${r.provider}/${r.account}`, r]));
  const passive = new Map((balances.data.value?.passive ?? []).map((q) => [q.provider, q]));
  const loginable = new Set(loginStations.data.value ?? []);
  const now = Date.now();
  return (providers.data.value ?? [])
    .filter((p) => !p.keyless || loginable.has(p.id) || providerForms.data.value?.some((entry) => entry.id === p.id))
    .filter((p) => `${p.id} ${p.display_name}`.toLowerCase().includes(providerSearch.value.toLowerCase().trim()))
    .map((p) => {
      const accounts: AccountRow[] = p.account_ids.map((id) => {
        const state = status.get(`${p.id}/${id}`);
        const row = balance.get(`${p.id}/${id}`);
        return {
          id,
          needsLogin: state?.needs_login ?? false,
          expiresAt: state?.expires_at ?? null,
          balance: row?.available ?? null,
          unit: row?.unit ?? 'credits',
          balanceNote: row?.message ?? '',
        };
      });
      return {
        id: p.id,
        name: p.display_name,
        enabled: p.enabled,
        loginable: loginable.has(p.id),
        addable: p.id === 'agg' || !!providerForms.data.value?.some((entry) => entry.id === p.id && entry.forms.length),
        accounts,
        needsLogin: accounts.filter((a) => a.needsLogin).length,
        expired: accounts.filter((a) => a.expiresAt !== null && a.expiresAt <= now).length,
        quota: passive.get(p.id) ?? null,
      };
    });
});

const empty = computed(() => !cards.value.length);

// -- what a lane says about its own allowance -------------------------------- #
/**
 * The badge a lane earns from the allowance it reported on the responses it was
 * already sending — never a balance round trip. The decision itself lives in
 * `quotaBadge`, so it can be tested; only the words are made here.
 */
function quotaLabel(quota: PassiveQuota | null): { tone: 'error' | 'warning' | 'primary'; value: string } | null {
  const badge = quotaBadge(quota ? { percent: quota.percent, stale: quota.stale, rateLimited: quota.counters.rate_limited } : null);
  if (!badge || !quota) return null;
  if (badge.kind === 'limited') return { tone: badge.tone, value: t('channels.quotaLimited', { n: quota.counters.rate_limited }) };
  if (badge.kind === 'stale') return { tone: badge.tone, value: t('channels.quotaStale', { pct: quota.percent! }) };
  return { tone: badge.tone, value: t('channels.quota', { pct: quota.percent! }) };
}
/** A dialect this store can no longer read is worth naming even when a number still came through. */
function quotaDrift(quota: PassiveQuota | null): string | null {
  const drift = quota?.counters.quota_header_drift ?? 0;
  return drift > 0 ? t('channels.quotaDrift', { n: drift }) : null;
}

/** The daily sweep's own memory, so "today" is answerable without a claim round. */
const checkin = computed(() => balances.data.value?.checkin);
const checkedToday = computed(() => !!checkin.value?.last_date);
const checkinWhen = computed(() => (checkin.value?.last_at ? new Date(checkin.value.last_at * 1000).toLocaleString() : '—'));

// -- the automation switches ------------------------------------------------ #
function save(patch: Record<string, unknown>) {
  update.mutate(patch, {
    onSuccess: () => snackbar.show(t('common.saved')),
    onError: (e) => snackbar.error((e as Error).message),
  });
}
const autoRefresh = computed({
  get: () => settings.data.value?.server.auto_refresh ?? true,
  set: (value: boolean) => save({ server: { auto_refresh: value } }),
});
const autoCheckin = computed({
  get: () => settings.data.value?.server.auto_checkin ?? true,
  set: (value: boolean) => save({ server: { auto_checkin: value } }),
});

// -- actions ---------------------------------------------------------------- #
function act(mutation: { mutate: (v: never, opts: Record<string, unknown>) => void }, value: unknown, done: string) {
  mutation.mutate(value as never, {
    onSuccess: () => snackbar.show(done),
    onError: (e: Error) => snackbar.error(e.message),
  });
}
const claimAll = () => act(claim, null, t('channels.claimStarted'));
const claimOne = (provider: string) => act(claim, provider, t('channels.claimStarted'));
const refreshAll = () => act(refreshNow, null, t('channels.refreshStarted'));
const refreshOne = (provider: string) => act(refreshNow, provider, t('channels.refreshStarted'));

const confirming = ref(false);
const forgetTarget = ref<{ provider: string; account: string } | null>(null);
function askDelete(provider: string, account: string) {
  forgetTarget.value = { provider, account };
  confirming.value = true;
}
function doDelete() {
  if (!forgetTarget.value) return;
  remove.mutate(forgetTarget.value, {
    onSuccess: () => {
      confirming.value = false;
      forgetTarget.value = null;
      snackbar.show(t('channels.accountRemoved'));
    },
    onError: (e) => snackbar.error((e as Error).message),
  });
}

// -- the login dialog ------------------------------------------------------- #
const loggingIn = ref(false);
const session = ref<LoginSession | null>(null);
const loginError = ref('');
const manualCode = ref('');
const submittingCode = ref(false);
let polling = false;
let poller: number | undefined;
let opened = false;
let loginGeneration = 0;

async function startStationLogin(provider: string) {
  closeLogin();
  const generation = ++loginGeneration;
  loginError.value = '';
  manualCode.value = '';
  session.value = null;
  opened = false;
  loggingIn.value = true;
  try {
    const started = await startLogin(provider);
    if (generation !== loginGeneration || !loggingIn.value) {
      if (started.status === 'pending') await cancelLogin(started.id);
      return;
    }
    session.value = started;
    poller = window.setInterval(pollSession, 1500);
    await pollSession();
  } catch (e) {
    if (generation === loginGeneration) loginError.value = (e as Error).message;
  }
}

async function pollSession() {
  const id = session.value?.id;
  if (!id || polling) return;
  polling = true;
  try {
    const view = await pollLogin(id);
    if (session.value?.id !== id || !loggingIn.value) return;
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
        closeLogin();
      } else {
        loginError.value = view.message || t('common.error');
      }
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
  opened = false;
}
function closeLogin() {
  loginGeneration++;
  stopPolling();
  loggingIn.value = false;
  if (session.value?.status === 'pending') void cancelLogin(session.value.id).catch((e: Error) => snackbar.error(e.message));
  session.value = null;
  manualCode.value = '';
}
onBeforeUnmount(closeLogin);

async function sendManualCode() {
  if (!session.value || !manualCode.value.trim()) return;
  submittingCode.value = true;
  try {
    await submitLoginInput(session.value.id, manualCode.value);
    manualCode.value = '';
    loginError.value = '';
  } catch (e) {
    loginError.value = (e as Error).message;
  } finally {
    submittingCode.value = false;
  }
}

// -- adding an account -------------------------------------------------------- #
/** A value no station id can hold, so "paste the address yourself" is a real choice. */
const CUSTOM_STATION = '__custom__';

const adding = ref(false);
const addProvider = ref('agg');
const station = ref<string | null>(CUSTOM_STATION);
const baseUrl = ref('');
const apiKey = ref('');
const accountId = ref('');
const addError = ref('');
const authMethod = ref<string | null>(null);
const credentialValues = ref<Record<string, string | number | null>>({});
const nativeForms = computed(() => providerForms.data.value?.find((entry) => entry.id === addProvider.value)?.forms ?? []);
const nativeForm = computed(() => nativeForms.value.find((form) => form.method === authMethod.value) ?? nativeForms.value[0]);
const methodOptions = computed(() => nativeForms.value.map((form) => ({ value: form.method, label: t(`channels.methods.${form.method}`) })));
watch(authMethod, () => { credentialValues.value = { ...nativeForm.value?.defaults }; });

const chosen = computed(() => stations.data.value?.find((s) => s.id === station.value) ?? null);
const stationOptions = computed(() => [
  { value: CUSTOM_STATION, label: t('channels.addCustom') },
  ...(stations.data.value ?? []).map((s) => ({ value: s.id, label: s.display_name })),
]);
/** The station's own address and the variable it reads its key from — facts, so not translated. */
const stationHint = computed(() => (chosen.value ? `${chosen.value.base_url} · ${chosen.value.key_env}` : t('channels.addStationHelp')));
const canAdd = computed(() => addProvider.value === 'agg'
  ? !!apiKey.value.trim() && (!!chosen.value || !!baseUrl.value.trim())
  : !!nativeForm.value && nativeForm.value.fields.every((field) => !field.required || String(credentialValues.value[field.key] ?? '').trim()));

function openAdd(provider: string) {
  addProvider.value = provider;
  credentialValues.value = {};
  authMethod.value = nativeForms.value[0]?.method ?? null;
  credentialValues.value = { ...nativeForm.value?.defaults };
  addError.value = '';
  adding.value = true;
}
/** Runs on close whatever closed it, so a key never sits in a form the operator cannot see. */
function resetAdd() {
  station.value = CUSTOM_STATION;
  baseUrl.value = '';
  apiKey.value = '';
  accountId.value = '';
  addError.value = '';
  credentialValues.value = {};
}

function submitAdd() {
  let account: Record<string, unknown>;
  if (addProvider.value === 'agg') {
    account = { api_key: apiKey.value.trim() };
    const preset = chosen.value;
    if (preset) account.preset = preset.id;
    else account.base_url = baseUrl.value.trim();
  } else {
    if (!nativeForm.value) return;
    try {
      account = accountFromForm(nativeForm.value, credentialValues.value);
    } catch (error) {
      addError.value = error instanceof AccountFormError ? t(error.reason === 'json' ? 'channels.invalidJson' : error.reason === 'choice' ? 'channels.invalidChoice' : 'channels.fieldRequired', { field: t(`channels.fields.${error.field}`) }) : t('common.error');
      return;
    }
  }
  const wanted = accountId.value.trim();
  if (wanted) account.id = wanted;
  addError.value = '';
  add.mutate(
    { provider: addProvider.value, account },
    {
      onSuccess: (row) => {
        adding.value = false;
        snackbar.show(t('channels.accountAdded', { id: row.account }));
      },
      onError: (error) => {
        addError.value = error.message || t('common.error');
      },
    },
  );
}

const expiry = (ms: number | null) => (ms ? new Date(ms).toLocaleString() : '—');
</script>

<template>
  <div class="channels">
    <Surface :level="0" shape="large" class="section">
      <div class="section-head">
        <h2 class="type-title-large">{{ t('channels.automation') }}</h2>
        <IconButton :icon="icons.RefreshCw" :label="t('common.refresh')" :spin="providers.isFetching.value" @click="providers.refetch()" />
      </div>
      <div class="toggles">
        <Switch v-model="autoCheckin" :label="t('channels.autoCheckin')" :supporting-text="t('channels.autoCheckinHelp')" />
        <Switch v-model="autoRefresh" :label="t('channels.autoRefresh')" :supporting-text="t('channels.autoRefreshHelp')" />
      </div>
      <div class="checkin-row">
        <div class="checkin-text">
          <span class="type-body-medium">{{ t('channels.todayCheckin') }}</span>
          <span class="type-body-small muted">
            {{ checkedToday ? t('channels.checkedAt', { when: checkinWhen }) : t('channels.notChecked') }}
            <template v-if="checkin?.last_result"> · {{ checkin.last_result }}</template>
          </span>
        </div>
        <div class="actions">
          <AppButton variant="tonal" :icon="icons.Check" :loading="claim.isPending.value" @click="claimAll">{{ t('channels.claimNow') }}</AppButton>
          <AppButton variant="outlined" :icon="icons.RotateCcw" :loading="refreshNow.isPending.value" @click="refreshAll">{{ t('channels.refreshNow') }}</AppButton>
        </div>
      </div>
    </Surface>

    <TextField v-model="providerSearch" type="search" :label="t('channels.search')" />
    <div v-if="providers.isPending.value" class="skeletons"><Skeleton v-for="i in 3" :key="i" height="120px" shape="medium" /></div>
    <EmptyState v-else-if="empty" :icon="icons.Waves" :title="t('channels.empty')" :text="t('channels.emptyText')" />
    <div v-else class="cards">
      <Surface v-for="card in cards" :key="card.id" :level="0" shape="large" class="card">
        <div class="card-head">
          <div class="card-text">
            <span class="type-title-small name">{{ card.name }}</span>
            <span class="type-body-small muted">{{ t('channels.accountCount', { n: card.accounts.length }) }}</span>
          </div>
          <div class="card-badges">
            <Badge v-if="!card.enabled" tone="neutral" :value="t('channels.disabled')" />
            <Badge v-if="card.needsLogin" tone="warning" :value="t('channels.needsLogin', { n: card.needsLogin })" />
            <Badge v-if="card.expired" tone="error" :value="t('channels.expired', { n: card.expired })" />
            <Badge v-if="quotaLabel(card.quota)" :tone="quotaLabel(card.quota)!.tone" :value="quotaLabel(card.quota)!.value" />
            <Badge v-if="quotaDrift(card.quota)" tone="warning" :value="quotaDrift(card.quota)!" />
          </div>
          <div class="card-actions">
            <AppButton v-if="card.addable" variant="filled" :icon="icons.Plus" @click="openAdd(card.id)">{{ t('channels.addAccount') }}</AppButton>
            <AppButton v-if="card.loginable" variant="filled" :icon="icons.Fingerprint" @click="startStationLogin(card.id)">{{ t('channels.login') }}</AppButton>
            <AppButton v-if="card.accounts.length" variant="tonal" :icon="icons.RotateCcw" @click="refreshOne(card.id)">{{ t('common.refresh') }}</AppButton>
            <AppButton v-if="card.accounts.length" variant="outlined" :icon="icons.Check" @click="claimOne(card.id)">{{ t('channels.claimNow') }}</AppButton>
          </div>
        </div>
        <ul v-if="card.accounts.length" class="list" role="list">
          <li v-for="account in card.accounts" :key="account.id" class="row">
            <div class="row-text">
              <span class="mono type-body-medium id">{{ account.id }}</span>
              <span class="type-body-small muted">
                {{ t('channels.expires', { when: expiry(account.expiresAt) }) }}
                <template v-if="account.balance !== null"> · {{ t('channels.balance', { n: account.balance, unit: account.unit }) }}</template>
                <template v-else-if="account.balanceNote"> · {{ account.balanceNote }}</template>
              </span>
            </div>
            <Badge v-if="account.needsLogin" tone="warning" :value="t('channels.needsLoginShort')" />
            <IconButton :icon="icons.Trash2" :label="t('channels.forget')" @click="askDelete(card.id, account.id)" />
          </li>
        </ul>
        <p v-else class="type-body-small muted">{{ card.loginable ? t('channels.signInHint') : t('channels.noAccounts') }}</p>
      </Surface>
    </div>

    <AppDialog v-model:open="adding" :title="t('channels.addTitle')" width="medium" @closed="resetAdd">
      <div class="add-body">
        <template v-if="addProvider === 'agg'">
          <SelectField v-model="station" :label="t('channels.addStation')" :options="stationOptions" :supporting-text="stationHint" />
          <TextField v-if="!chosen" v-model="baseUrl" type="url" :label="t('channels.addBaseUrl')" placeholder="https://openrouter.ai/api/v1" :supporting-text="t('channels.addBaseUrlHelp')" />
          <TextField v-model="apiKey" type="password" autocomplete="off" :label="t('channels.addKey')" :icon="icons.KeyRound" :supporting-text="t('channels.addKeyHelp')" />
        </template>
        <template v-else>
          <SelectField v-if="nativeForms.length > 1" v-model="authMethod" :options="methodOptions" :label="t('channels.authMethod')" />
          <p v-if="addProvider === 'eac' && authMethod === 'bundled'" class="type-body-small muted">{{ t('channels.eacBundledHelp') }}</p>
          <template v-for="field in nativeForm?.fields ?? []" :key="`${authMethod}:${field.key}`">
            <SelectField v-if="field.kind === 'select'" :model-value="String(credentialValues[field.key] ?? '')" @update:model-value="credentialValues[field.key] = $event"
              :label="t(`channels.fields.${field.key}`)" :options="(field.choices ?? []).map(value => ({ value, label: t(`channels.protocols.${value}`) }))" />
            <TextField v-else v-model="credentialValues[field.key]"
              :type="field.kind === 'secret' ? 'password' : 'text'" autocomplete="off" :label="t(`channels.fields.${addProvider === 'eac' && field.key === 'base_url' ? 'relay_base_url' : field.key}`)"
              :supporting-text="field.kind === 'list' ? t('channels.modelListHelp') : field.key === 'base_url' ? t(addProvider === 'eac' ? 'channels.eacRelayHelp' : 'channels.baseUrlHelp') : field.kind === 'secret' ? t('channels.addKeyHelp') : undefined" />
          </template>
        </template>
        <TextField v-model="accountId" :label="t('channels.addAccountId')" :supporting-text="t('channels.addAccountIdHelp')" />
        <p v-if="addError" class="type-body-medium error-text">{{ addError }}</p>
      </div>
      <template #actions>
        <AppButton variant="text" :disabled="add.isPending.value" @click="adding = false">{{ t('common.cancel') }}</AppButton>
        <AppButton variant="filled" :loading="add.isPending.value" :disabled="!canAdd" @click="submitAdd">{{ t('channels.addSave') }}</AppButton>
      </template>
    </AppDialog>

    <AppDialog v-model:open="loggingIn" :title="t('channels.loginTitle')" width="medium" @closed="closeLogin">
      <div class="login-body">
        <p v-if="!session" class="type-body-medium muted">{{ t('channels.loginStarting') }}</p>
        <template v-else>
          <p v-if="session.login_url" class="type-body-medium">{{ t('channels.loginOpened') }}</p>
          <p v-else class="type-body-medium muted">{{ t('channels.loginWaiting') }}</p>
          <a v-if="session.login_url" :href="session.login_url" target="_blank" rel="noopener" class="type-body-small link mono">{{ session.login_url }}</a>
          <p v-if="session.user_code" class="type-body-medium">{{ t('channels.deviceCode') }}: <strong class="mono">{{ session.user_code }}</strong></p>
          <p v-if="session.user_code" class="type-body-small muted">{{ t('channels.deviceInstructions') }}</p>
          <p v-if="session.expires_at" class="type-body-small muted">{{ t('channels.expires', { when: new Date(session.expires_at * 1000).toLocaleString() }) }}</p>
          <template v-if="session.waiting_for === 'manual_code' && session.status === 'pending'">
            <TextField v-model="manualCode" type="password" autocomplete="off" :label="t('channels.manualCode')" :supporting-text="t('channels.manualCodeHelp')" @enter="sendManualCode" />
            <AppButton :loading="submittingCode" :disabled="!manualCode.trim()" @click="sendManualCode">{{ t('channels.submitCode') }}</AppButton>
          </template>
          <p v-if="session.status === 'succeeded'" class="type-body-medium ok-text">{{ t('channels.loginDone', { id: session.account_id }) }}</p>
        </template>
        <p v-if="loginError" class="type-body-medium error-text">{{ loginError }}</p>
      </div>
      <template #actions>
        <AppButton variant="text" @click="closeLogin">{{ t('common.close') }}</AppButton>
      </template>
    </AppDialog>

    <ConfirmDialog
      v-model:open="confirming"
      :title="t('channels.forgetTitle')"
      :message="t('channels.forgetMessage')"
      :confirm-label="t('channels.forget')"
      :cancel-label="t('common.cancel')"
      danger
      :loading="remove.isPending.value"
      @confirm="doDelete"
    />
  </div>
</template>

<style scoped>
.channels { display: flex; flex-direction: column; gap: var(--app-space-4); width: 100%; padding: var(--app-space-4) var(--app-space-6) var(--app-space-8); }
.section-head { display: flex; align-items: center; justify-content: space-between; gap: var(--app-space-3); }
h1, h2, p { margin: 0; }
.skeletons { display: flex; flex-direction: column; gap: var(--app-space-4); }
.section { display: flex; flex-direction: column; gap: var(--app-space-3); padding: var(--app-space-4) var(--app-space-6) var(--app-space-6); }
.toggles { display: grid; grid-template-columns: repeat(auto-fill, minmax(260px, 1fr)); gap: var(--app-space-3); }
.checkin-row { display: flex; align-items: center; justify-content: space-between; gap: var(--app-space-4); flex-wrap: wrap; padding-top: var(--app-space-2); border-top: 1px solid var(--md-sys-color-outline-variant); }
.checkin-text { display: flex; flex-direction: column; gap: var(--app-space-1); min-width: 0; }
.actions, .card-actions { display: flex; align-items: center; gap: var(--app-space-2); flex-wrap: wrap; }
.cards { display: flex; flex-direction: column; gap: var(--app-space-3); }
.card { display: flex; flex-direction: column; gap: var(--app-space-3); padding: var(--app-space-4) var(--app-space-6) var(--app-space-4); }
.card-head { display: flex; align-items: center; gap: var(--app-space-3); flex-wrap: wrap; }
.card-text { display: flex; flex-direction: column; gap: var(--app-space-1); min-width: 0; flex: 1 1 200px; }
.card-badges { display: flex; align-items: center; gap: var(--app-space-2); flex-wrap: wrap; }
.name { overflow-wrap: anywhere; }
.list { display: flex; flex-direction: column; gap: var(--app-space-1); margin: 0; padding: 0; list-style: none; }
.row { display: flex; align-items: center; justify-content: space-between; gap: var(--app-space-3); padding: var(--app-space-2) var(--app-space-3); border-radius: var(--md-sys-shape-corner-small); background: var(--md-sys-color-surface-container); }
.row-text { display: flex; flex-direction: column; min-width: 0; }
.id { overflow-wrap: anywhere; }
.mono { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }
.login-body { display: flex; flex-direction: column; gap: var(--app-space-2); }
.add-body { display: flex; flex-direction: column; gap: var(--app-space-3); }
.link { color: var(--md-sys-color-primary); overflow-wrap: anywhere; }
.error-text { color: var(--md-sys-color-error); }
.ok-text { color: var(--md-sys-color-primary); }
@media (max-width: 599px) {
  .channels { padding: var(--app-space-3); }
  .section, .card { padding: var(--app-space-4); }
}
</style>
