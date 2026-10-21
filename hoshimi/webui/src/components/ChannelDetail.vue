<script setup lang="ts">
import { computed, ref } from 'vue';
import { useRouter } from 'vue-router';
import { useUpdateSettings } from '@/api/queries/app';
import { useClaimCredits, useDeleteAccount, useRefreshNow, type BalanceReport } from '@/api/queries/channels';
import type { ChannelAccount, ChannelView } from '@/api/types';
import AddAccountDialog from '@/components/AddAccountDialog.vue';
import LoginDialog from '@/components/LoginDialog.vue';
import NetworkSettings from '@/components/NetworkSettings.vue';
import { compact, percent, ratio, relative, when } from '@/format';
import { useI18n } from '@/i18n';
import {
  AppButton,
  AppIcon,
  AppMenu,
  Badge,
  ConfirmDialog,
  DataList,
  EmptyState,
  IconButton,
  Meter,
  PageSection,
  ScrollArea,
  SelectField,
  Switch,
  TRANSITIONS,
  Tooltip,
  TweenNumber,
  icons,
  keepLeavingInPlace,
  useSnackbar,
  type MenuItem,
} from '@/ui';

/**
 * Everything about one channel on one page: whether it is on, signing in or adding a credential
 * (its one main action), what needs a hand, its accounts with their renewal state, balance and
 * use today, the allowance the station reports, and its own network route. The provider switch
 * and network used to live in Settings; they are here so a channel is managed in one place.
 */
const props = defineProps<{ channel: ChannelView; balances: BalanceReport | undefined; back?: boolean }>();
const { t, locale } = useI18n();
const router = useRouter();
const snackbar = useSnackbar();
const update = useUpdateSettings();
const claim = useClaimCredits();
const refreshNow = useRefreshNow();
const remove = useDeleteAccount();
const c = computed(() => props.channel);

function save(patch: Record<string, unknown>, done = t('common.saved')) {
  update.mutate(patch, { onSuccess: () => snackbar.show(done), onError: (e) => snackbar.error((e as Error).message) });
}
const enabled = computed({
  get: () => c.value.enabled,
  set: (value: boolean) => save({ providers: { [c.value.id]: { enabled: value } } }, value ? t('channels.enabledNow') : t('channels.disabledNow')),
});

// -- accounts ------------------------------------------------------------------ #
const now = () => Date.now();
const balanceOf = (account: string) => props.balances?.rows.find((r) => r.provider === c.value.id && r.account === account);
type AccountState = 'login' | 'expired' | 'cooled' | 'failing' | 'ok';
function stateOf(a: ChannelAccount): AccountState {
  if (a.needs_login) return 'login';
  if (a.expires_at !== null && a.expires_at !== undefined && a.expires_at <= now()) return 'expired';
  if (a.cooled_reason) return 'cooled';
  if (a.failures > 0 && a.last_error) return 'failing';
  return 'ok';
}
const TONES: Record<AccountState, 'error' | 'warning' | 'primary' | 'neutral'> = { login: 'warning', expired: 'error', cooled: 'warning', failing: 'warning', ok: 'primary' };
const accounts = computed(() =>
  c.value.accounts.map((a) => {
    const state = stateOf(a);
    const balance = balanceOf(a.id);
    const parts: string[] = [];
    if (a.expires_at) parts.push(t(a.expires_at <= now() ? 'channels.expiredAgo' : 'channels.expiresIn', { when: relative(a.expires_at - now(), locale.value) }));
    if (balance?.available !== null && balance?.available !== undefined) parts.push(t('channels.balance', { n: compact(balance.available), unit: balance.unit }));
    else if (balance?.message) parts.push(balance.message);
    if (a.cooled_reason) parts.push(t('channels.cooledFor', { reason: t(`channels.cooled.${a.cooled_reason}`), when: relative(a.cooled_seconds * 1000, locale.value) }));
    return {
      ...a,
      state,
      tone: TONES[state],
      line: parts.join(' · '),
      tokens: a.usage_today.input + a.usage_today.output,
      lastUsed: a.last_used_at ? relative(new Date(a.last_used_at).getTime() - now(), locale.value) : '',
      exact: a.expires_at ? new Date(a.expires_at).toLocaleString() : '',
    };
  }),
);
const accountMenu = (refreshable: boolean): MenuItem[] => [
  ...(refreshable ? [{ id: 'refresh', label: t('channels.renewAccount'), icon: icons.RotateCcw }] : []),
  { id: 'forget', label: t('channels.forget'), icon: icons.Trash2, danger: true },
];
const forgetting = ref<string | null>(null);
const confirmOpen = computed({ get: () => forgetting.value !== null, set: (v: boolean) => !v && (forgetting.value = null) });
function onAccountMenu(id: string, account: string) {
  if (id === 'forget') forgetting.value = account;
  else if (id === 'refresh') act(refreshNow, c.value.id, t('channels.refreshStarted'));
}
function doForget() {
  const account = forgetting.value;
  if (!account) return;
  remove.mutate(
    { provider: c.value.id, account },
    {
      onSuccess: () => {
        forgetting.value = null;
        snackbar.show(t('channels.accountRemoved'));
      },
      onError: (e) => snackbar.error((e as Error).message),
    },
  );
}

// -- actions ------------------------------------------------------------------- #
function act(mutation: { mutate: (v: never, opts: Record<string, unknown>) => void }, value: unknown, done: string) {
  mutation.mutate(value as never, { onSuccess: () => snackbar.show(done), onError: (e: Error) => snackbar.error(e.message) });
}
const adding = ref(false);
const loggingIn = ref(false);
const hasAccounts = computed(() => c.value.accounts.length > 0);
const more = computed<MenuItem[]>(() => [
  ...(hasAccounts.value ? [{ id: 'refresh', label: t('channels.refreshNow'), icon: icons.RotateCcw }, { id: 'claim', label: t('channels.claimNow'), icon: icons.CalendarCheck }] : []),
  // With both ways in, the one that is not the main action goes here.
  ...(c.value.loginable && c.value.addable ? [{ id: 'add', label: t('channels.addAccount'), icon: icons.KeyRound }] : []),
  { id: 'usage', label: t('channels.openUsage'), icon: icons.BarChart3 },
]);
function onMore(id: string) {
  if (id === 'refresh') act(refreshNow, c.value.id, t('channels.refreshStarted'));
  else if (id === 'claim') act(claim, c.value.id, t('channels.claimStarted'));
  else if (id === 'add') adding.value = true;
  else if (id === 'usage') void router.push('/usage');
}

// -- what needs a hand ------------------------------------------------------------ #
const problems = computed(() => {
  const out: string[] = [];
  const login = accounts.value.filter((a) => a.state === 'login').length;
  const expired = accounts.value.filter((a) => a.state === 'expired').length;
  const cooled = accounts.value.filter((a) => a.state === 'cooled').length;
  if (login) out.push(t('channels.needsLogin', { n: login }));
  if (expired) out.push(t('channels.expired', { n: expired }));
  if (cooled) out.push(t('channels.cooledCount', { n: cooled }));
  const q = c.value.quota;
  if (q?.counters.rate_limited) out.push(t('channels.quotaLimited', { n: q.counters.rate_limited }));
  if (q?.counters.quota_header_drift) out.push(t('channels.quotaDrift', { n: q.counters.quota_header_drift }));
  if (q && q.percent !== null && q.percent !== undefined && q.percent <= 10) out.push(t('channels.quotaLow', { pct: q.percent }));
  return out;
});
const failures = computed(() => c.value.diagnostics.filter((d) => d.kind !== 'ok'));

// -- allowance ------------------------------------------------------------------ #
const quota = computed(() => c.value.quota);
const quotaTone = computed(() => {
  const p = quota.value?.percent;
  return p === null || p === undefined ? 'primary' : p <= 10 ? 'error' : p <= 25 ? 'warning' : 'primary';
});

// -- today ------------------------------------------------------------------- #
const today = computed(() => c.value.usage_today);
</script>

<template>
  <div class="detail">
    <header class="head">
      <IconButton v-if="back" :icon="icons.ArrowLeft" :label="t('channels.backToList')" @click="router.push('/channels')" />
      <div class="titles">
        <h2 class="type-headline-small name">{{ c.display_name }}</h2>
        <span class="type-body-small muted mono">{{ c.id }}</span>
      </div>
      <Switch v-model="enabled" inline :label="t('common.enabled')" />
    </header>
    <div class="actions">
      <AppButton v-if="c.loginable" variant="filled" :icon="icons.LogIn" @click="loggingIn = true">{{ t('channels.login') }}</AppButton>
      <AppButton v-else-if="c.addable" variant="filled" :icon="icons.Plus" @click="adding = true">{{ t('channels.addAccount') }}</AppButton>
      <AppButton v-if="hasAccounts" variant="tonal" :icon="icons.RotateCcw" :loading="refreshNow.isPending.value" @click="act(refreshNow, c.id, t('channels.refreshStarted'))">{{ t('channels.refreshNow') }}</AppButton>
      <AppMenu :items="more" @select="onMore">
        <template #default="{ toggle }"><IconButton :icon="icons.MoreVertical" :label="t('common.more')" @click="toggle" /></template>
      </AppMenu>
    </div>

    <Transition :name="TRANSITIONS.collapse">
      <div v-if="problems.length" class="banner" role="status">
        <AppIcon :icon="icons.AlertTriangle" :size="20" class="banner-icon" />
        <ul class="type-body-medium">
          <li v-for="p in problems" :key="p">{{ p }}</li>
        </ul>
      </div>
    </Transition>

    <PageSection :title="t('channels.accounts')" :subtitle="t('channels.accountCount', { n: c.accounts.length })" :icon="icons.Users">
      <EmptyState v-if="!accounts.length" :icon="icons.KeyRound" :title="t('channels.noAccounts')" :text="c.keyless ? t('channels.keylessHint') : c.loginable ? t('channels.signInHint') : t('channels.addHint')" />
      <ScrollArea v-else :label="t('channels.accounts')" max-height="min(var(--app-height-list), 420px)">
        <TransitionGroup :name="TRANSITIONS.list" tag="ul" class="accounts" v-bind="keepLeavingInPlace">
          <li v-for="a in accounts" :key="a.id" class="account">
            <span class="state-dot" :class="a.tone" aria-hidden="true" />
            <div class="account-text">
              <span class="type-body-large mono id" :title="a.id">{{ a.id }}</span>
              <span v-if="a.line" class="type-body-small muted line" :title="a.exact">{{ a.line }}</span>
              <span v-if="a.state === 'failing'" class="type-body-small error-text line" :title="a.last_error ?? ''">{{ a.last_error }}</span>
            </div>
            <div class="account-usage">
              <span class="type-label-large num">{{ compact(a.tokens) }}</span>
              <span class="type-body-small muted">{{ a.lastUsed ? t('channels.lastUsed', { when: a.lastUsed }) : t('channels.tokensToday') }}</span>
            </div>
            <Badge :tone="a.tone" :value="t(`channels.accountStates.${a.state}`)" />
            <AppMenu :items="accountMenu(a.refreshable)" @select="onAccountMenu($event, a.id)">
              <template #default="{ toggle }"><IconButton :icon="icons.MoreVertical" :label="t('channels.accountActions', { id: a.id })" @click="toggle" /></template>
            </AppMenu>
          </li>
        </TransitionGroup>
      </ScrollArea>
    </PageSection>

    <PageSection :title="t('channels.today')" :icon="icons.BarChart3">
      <dl class="today">
        <div><dt class="type-label-medium muted">{{ t('usage.tokens') }}</dt><dd class="type-title-large"><TweenNumber :value="today.input + today.output" :format="compact" /></dd></div>
        <div><dt class="type-label-medium muted">{{ t('usage.calls') }}</dt><dd class="type-title-large"><TweenNumber :value="today.calls" :format="compact" /></dd></div>
        <div><dt class="type-label-medium muted">{{ t('usage.cacheHit') }}</dt><dd class="type-title-large">{{ percent(ratio(today.cache_read, today.input)) }}</dd></div>
        <div><dt class="type-label-medium muted">{{ t('usage.failed') }}</dt><dd class="type-title-large"><TweenNumber :value="today.failed" :format="compact" /></dd></div>
      </dl>
    </PageSection>

    <PageSection v-if="quota" :title="t('channels.allowance')" :subtitle="quota.stale ? t('channels.allowanceStale') : t('channels.allowanceHint')" :icon="icons.Gauge">
      <div v-if="quota.percent !== null && quota.percent !== undefined" class="quota">
        <Meter :value="quota.percent / 100" :tone="quotaTone" :label="t('channels.quota', { pct: quota.percent })" :height="8" />
        <span class="type-label-large num">{{ quota.percent }}%</span>
      </div>
      <DataList
        :rows="[
          { label: t('channels.remaining'), value: quota.remaining !== null && quota.remaining !== undefined ? `${compact(quota.remaining)}${quota.limit ? ` / ${compact(quota.limit)}` : ''}` : null },
          { label: t('channels.quotaSource'), value: quota.source || null, mono: true },
          { label: t('channels.updated'), value: quota.updated_at ? relative(quota.updated_at * 1000 - Date.now(), locale) : null },
          { label: t('channels.responses'), value: quota.counters.responses },
          { label: t('channels.rateLimited'), value: quota.counters.rate_limited },
          { label: t('channels.authFailures'), value: quota.counters.auth_failures },
          { label: t('channels.serverErrors'), value: quota.counters.server_errors },
        ]"
      />
    </PageSection>

    <PageSection v-if="c.recent_checkins.length" :title="t('channels.recentCheckins')" :subtitle="t('channels.recentCheckinsHint')" :icon="icons.CalendarCheck">
      <ul class="checkins">
        <li v-for="k in c.recent_checkins" :key="`${k.at}${k.account}`" class="checkin">
          <Badge :tone="k.ok ? 'primary' : 'error'" :value="k.ok ? t('channels.checkinOk') : t('channels.checkinFailed')" />
          <span class="checkin-text">
            <span class="type-body-medium mono id" :title="k.account">{{ k.account || '—' }}</span>
            <span v-if="k.message" class="type-body-small muted line" :title="k.message">{{ k.message }}</span>
          </span>
          <span v-if="k.credits" class="type-label-large num">+{{ compact(k.credits) }}</span>
          <time class="type-body-small muted" :datetime="k.at" :title="when(k.at)">{{ relative(Date.parse(k.at) - Date.now(), locale) }}</time>
        </li>
      </ul>
    </PageSection>

    <PageSection :title="t('network.title')" :subtitle="t('channels.networkHint')" :icon="icons.Network">
      <NetworkSettings :value="c.network" provider @save="save({ providers: { [c.id]: { options: { network: $event } } } })" />
      <SelectField
        v-if="c.id === 'cline'"
        :model-value="c.client_profile ?? 'sdk'"
        :label="t('network.clineProfile')"
        :options="[{ value: 'sdk', label: t('network.deviceLogin') }, { value: 'extension', label: t('network.browserLogin') }]"
        @update:model-value="save({ providers: { cline: { options: { client_profile: $event } } } })"
      />
      <ul v-if="failures.length" class="diagnostics">
        <li v-for="d in failures" :key="d.operation" class="type-body-small">
          <Tooltip :text="`${d.host} · ${d.route}`"><span class="diag-op">{{ t(`network.operations.${d.operation}`) }}</span></Tooltip>
          <span class="error-text">{{ t(`network.errors.${d.kind}`) }}<template v-if="d.status"> · HTTP {{ d.status }}</template></span>
          <span class="muted">{{ relative(d.at * 1000 - Date.now(), locale) }}</span>
        </li>
      </ul>
    </PageSection>

    <AddAccountDialog v-model:open="adding" :provider="c.id" :name="c.display_name" />
    <LoginDialog v-model:open="loggingIn" :provider="c.id" :name="c.display_name" />
    <ConfirmDialog
      v-model:open="confirmOpen"
      :title="t('channels.forgetTitle')"
      :message="t('channels.forgetMessage', { id: forgetting ?? '' })"
      :confirm-label="t('channels.forget')"
      :cancel-label="t('common.cancel')"
      danger
      :loading="remove.isPending.value"
      @confirm="doForget"
    />
  </div>
</template>

<style scoped>
.detail { display: flex; flex-direction: column; gap: var(--app-space-4); min-width: 0; }
.head { display: flex; align-items: center; gap: var(--app-space-3); min-width: 0; }
.titles { display: flex; flex-direction: column; flex: 1; min-width: 0; }
.name { margin: 0; overflow-wrap: anywhere; }
.actions { display: flex; align-items: center; flex-wrap: wrap; gap: var(--app-space-2); }
.banner {
  display: flex; align-items: flex-start; gap: var(--app-space-3); padding: var(--app-space-3) var(--app-space-4);
  border-radius: var(--md-sys-shape-corner-medium); background: var(--md-sys-color-tertiary-container); color: var(--md-sys-color-on-tertiary-container);
}
.banner-icon { flex: none; margin-top: 2px; }
.banner ul { display: flex; flex-direction: column; gap: var(--app-space-1); margin: 0; padding: 0; list-style: none; }
.accounts { position: relative; display: flex; flex-direction: column; gap: var(--app-space-1); margin: 0; padding: 0; list-style: none; }
.account {
  display: flex; align-items: center; gap: var(--app-space-3); min-height: 64px; padding: var(--app-space-2) var(--app-space-2) var(--app-space-2) var(--app-space-4);
  border-radius: var(--md-sys-shape-corner-medium); background: var(--md-sys-color-surface-container);
}
.state-dot { flex: none; width: 8px; height: 8px; border-radius: 50%; background: var(--md-sys-color-primary); }
.state-dot.warning { background: var(--md-sys-color-tertiary); }
.state-dot.error { background: var(--md-sys-color-error); }
.account-text { display: flex; flex-direction: column; flex: 1; min-width: 0; }
.id, .line { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.account-usage { display: flex; flex-direction: column; align-items: flex-end; text-align: end; }
.checkins { display: flex; flex-direction: column; gap: var(--app-space-2); margin: 0; padding: 0; list-style: none; }
.checkin { display: flex; align-items: center; gap: var(--app-space-3); min-width: 0; }
.checkin-text { display: flex; flex-direction: column; flex: 1; min-width: 0; }
.checkin-text .id, .checkin-text .line { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.today { display: grid; grid-template-columns: repeat(auto-fit, minmax(120px, 1fr)); gap: var(--app-space-3); margin: 0; }
.today div { display: flex; flex-direction: column; min-width: 0; }
.today dd { margin: 0; }
.quota { display: grid; grid-template-columns: minmax(0, 1fr) auto; align-items: center; gap: var(--app-space-3); }
.diagnostics { display: flex; flex-direction: column; gap: var(--app-space-1); margin: 0; padding: 0; list-style: none; }
.diagnostics li { display: flex; flex-wrap: wrap; gap: var(--app-space-2); }
.diag-op { font-weight: 500; }
.error-text { color: var(--md-sys-color-error); }
@container app-content (max-width: 599px) {
  .account-usage { display: none; }
}
</style>
