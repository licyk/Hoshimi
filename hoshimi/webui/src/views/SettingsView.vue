<script setup lang="ts">
import { computed, ref, watch } from 'vue';
import { useProviders, useSettings, useUpdateSettings, useVersion } from '@/api/queries/app';
import { useI18n } from '@/i18n';
import { usePreferencesStore } from '@/stores/preferences';
import { AppButton, Badge, DataList, SegmentedButton, SelectField, Skeleton, Slider, Surface, Switch, TextField, TokenField, icons, useSnackbar } from '@/ui';

const { t, localeOptions } = useI18n();
const settings = useSettings();
const providers = useProviders();
const update = useUpdateSettings();
const version = useVersion();
const snackbar = useSnackbar();
const prefs = usePreferencesStore();

const s = computed(() => settings.data.value);

/** Save one patch; each change saves on its own with a snackbar confirmation. */
function save(patch: Record<string, unknown>, restart = false) {
  update.mutate(patch, {
    onSuccess: () => snackbar.show(restart ? `${t('common.saved')} · ${t('common.restartNeeded')}` : t('common.saved')),
    onError: (e) => snackbar.error((e as Error).message),
  });
}
const num = (v: string | number | null) => (v === '' || v === null ? null : Number(v));
const list = (v: string) =>
  v
    .split(/[\s,]+/)
    .map((x) => x.trim())
    .filter(Boolean);
const saveProvider = (id: string, enabled: boolean) => save({ providers: { [id]: { enabled } } });
/**
 * Every card the switch list shows: the config's own entries, and lanes the API knows by id even
 * though this install has never configured them (that is how a lane needing an outside
 * authorization is offered instead of silently absent).
 */
const availableProviderCards = computed(() => {
  const runtime = new Map((providers.data.value ?? []).map((p) => [p.id, p]));
  const cards = (s.value?.providers ?? []).map((cfg) => ({
    id: cfg.id,
    display_name: runtime.get(cfg.id)?.display_name ?? cfg.id,
    accounts: cfg.accounts,
    options: cfg.options,
    enabled: cfg.enabled,
    requires_authorization: runtime.get(cfg.id)?.requires_authorization ?? false,
  }));
  for (const lane of providers.data.value ?? []) {
    if (cards.some((c) => c.id === lane.id)) continue;
    cards.push({
      id: lane.id,
      display_name: lane.display_name,
      accounts: lane.accounts,
      options: [],
      enabled: lane.enabled,
      requires_authorization: lane.requires_authorization,
    });
  }
  return cards;
});
// Saving a previously unconfigured provider changes the API's ordering. Keep the displayed
// positions across refreshes and append newly discovered providers at the end.
const providerOrder = ref<string[]>([]);
watch(availableProviderCards, (cards) => {
  const known = new Set(providerOrder.value);
  const added = cards.filter((card) => !known.has(card.id)).map((card) => card.id);
  if (added.length) providerOrder.value = [...providerOrder.value, ...added];
}, { immediate: true });
const providerCards = computed(() => {
  const cards = new Map(availableProviderCards.value.map((card) => [card.id, card]));
  return providerOrder.value.flatMap((id) => {
    const card = cards.get(id);
    return card ? [card] : [];
  });
});
</script>

<template>
  <div class="settings">
    <div v-if="!s" class="skeletons"><Skeleton v-for="i in 3" :key="i" height="160px" shape="medium" /></div>
    <template v-else>
      <Surface :level="0" shape="large" class="section">
        <h2 class="type-title-large">{{ t('settings.sections.server') }}</h2>
        <div class="grid">
          <TextField :model-value="s.server.host" :label="t('settings.host')" :supporting-text="t('common.restartNeeded')" @change="save({ server: { host: $event } }, true)" />
          <TextField :model-value="s.server.port" type="number" :label="t('settings.port')" :supporting-text="t('common.restartNeeded')" @change="save({ server: { port: num($event) } }, true)" />
          <TextField
            :model-value="s.server.default_model ?? ''"
            :label="t('settings.defaultModel')"
            :supporting-text="t('settings.defaultModelHelp')"
            @change="save({ server: { default_model: $event || null } })"
          />
          <TextField
            :model-value="s.server.max_body_bytes"
            type="number"
            :min="1"
            :label="t('settings.maxBodyBytes')"
            @change="save({ server: { max_body_bytes: num($event) } })"
          />
          <TextField
            :model-value="s.server.max_account_retries"
            type="number"
            :min="1"
            :label="t('settings.maxAccountRetries')"
            @change="save({ server: { max_account_retries: num($event) } })"
          />
        </div>
        <TokenField
          :label="t('settings.accessToken')"
          :configured="s.server.access_token_configured"
          :save-label="t('common.save')"
          :clear-label="t('common.clear')"
          :configured-text="t('settings.accessTokenConfigured')"
          :not-configured-text="t('settings.accessTokenHelp')"
          @save="save({ server: { access_token: $event } })"
          @clear="save({ server: { access_token: null } })"
        />
        <TokenField
          :label="t('settings.modelApiKey')"
          :configured="s.server.model_api_key_configured"
          :save-label="t('common.save')"
          :clear-label="t('common.clear')"
          :configured-text="t('settings.modelApiKeyConfigured')"
          :not-configured-text="t('settings.modelApiKeyHelp')"
          @save="save({ server: { model_api_key: $event } })"
          @clear="save({ server: { model_api_key: null } })"
        />
      </Surface>

      <Surface :level="0" shape="large" class="section">
        <h2 class="type-title-large">{{ t('settings.sections.security') }}</h2>
        <TextField
          :model-value="s.server.allow_cidrs.join(' ')"
          :label="t('settings.allowCidrs')"
          :supporting-text="t('settings.allowCidrsHelp')"
          @change="save({ security: { allow_cidrs: list($event) } })"
        />
        <TextField
          :model-value="s.server.allow_hosts.join(' ')"
          :label="t('settings.allowHosts')"
          :supporting-text="t('settings.allowHostsHelp')"
          @change="save({ security: { allow_hosts: list($event) } })"
        />
        <TextField
          :model-value="s.server.allow_origins.join(' ')"
          :label="t('settings.allowOrigins')"
          :supporting-text="t('settings.allowOriginsHelp')"
          @change="save({ security: { allow_origins: list($event) } })"
        />
      </Surface>

      <Surface :level="0" shape="large" class="section">
        <div class="section-head">
          <h2 class="type-title-large">{{ t('settings.sections.providers') }}</h2>
          <AppButton variant="tonal" :icon="icons.RefreshCw" :loading="settings.isFetching.value" @click="settings.refetch()">{{ t('common.refresh') }}</AppButton>
        </div>
        <p v-if="!providerCards.length" class="type-body-medium muted">{{ t('settings.providersEmpty') }}</p>
        <div v-else class="providers">
          <div v-for="p in providerCards" :key="p.id" class="provider">
            <div class="provider-text">
              <div class="provider-name">
                <span class="name type-title-small">{{ p.display_name }}</span>
                <Badge v-if="p.requires_authorization" tone="warning" :value="t('settings.needsAuthorization')" />
              </div>
              <span class="type-body-small muted">
                {{ t('settings.providerAccounts', { n: p.accounts }) }}
                <template v-if="p.options.length"> · {{ p.options.join(', ') }}</template>
              </span>
            </div>
            <Switch :model-value="p.enabled" :label="t('common.enabled')" @update:model-value="saveProvider(p.id, $event)" />
          </div>
        </div>
      </Surface>

      <Surface :level="0" shape="large" class="section">
        <h2 class="type-title-large">{{ t('settings.sections.appearance') }}</h2>
        <div class="field-row">
          <span class="type-body-large">{{ t('settings.theme') }}</span>
          <SegmentedButton
            v-model="prefs.prefs.theme"
            :options="[
              { value: 'light', icon: icons.Sun, label: t('settings.themes.light') },
              { value: 'dark', icon: icons.Moon, label: t('settings.themes.dark') },
              { value: 'system', icon: icons.SunMoon, label: t('settings.themes.system') },
            ]"
          />
        </div>
        <label class="field-row">
          <span class="type-body-large">{{ t('settings.sourceColor') }}</span>
          <input v-model="prefs.prefs.sourceColor" type="color" class="color" :aria-label="t('settings.sourceColor')" />
        </label>
        <Slider v-model="prefs.prefs.contrast" :label="t('settings.contrast')" :min="0" :max="1" :step="0.5" ticks />
        <div class="field-row">
          <span class="type-body-large">{{ t('settings.language') }}</span>
          <SelectField v-model="prefs.prefs.locale" :options="localeOptions" />
        </div>
      </Surface>

      <Surface :level="0" shape="large" class="section">
        <h2 class="type-title-large">{{ t('settings.sections.about') }}</h2>
        <DataList
          :rows="[
            { label: t('settings.version'), value: version.data.value?.version ?? '—' },
            { label: t('settings.dataDir'), value: s.data_dir, mono: true },
            { label: t('settings.configPath'), value: s.config_path, mono: true },
          ]"
        />
      </Surface>
    </template>
  </div>
</template>

<style scoped>
/* One column of sections, each as wide as the window allows. */
.settings { display: flex; flex-direction: column; align-items: stretch; gap: var(--app-space-4); width: 100%; padding: var(--app-space-4) var(--app-space-6) var(--app-space-8); }
.skeletons { display: flex; flex-direction: column; gap: var(--app-space-4); }
.section { display: flex; flex-direction: column; gap: var(--app-space-2); padding: var(--app-space-4) var(--app-space-6) var(--app-space-6); }
.section-head { display: flex; align-items: center; justify-content: space-between; gap: var(--app-space-3); flex-wrap: wrap; }
h2 { margin: 0 0 var(--app-space-2); }
p { margin: 0; }
.grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(240px, 1fr)); gap: var(--app-space-3); }
.field-row { display: flex; align-items: center; justify-content: space-between; gap: var(--app-space-4); min-height: 56px; flex-wrap: wrap; }
.field-row > .muted { min-width: 0; overflow-wrap: anywhere; }
.color { width: 56px; height: 40px; padding: 0; border: 1px solid var(--md-sys-color-outline); border-radius: var(--md-sys-shape-corner-small); background: transparent; cursor: pointer; }
.providers { display: flex; flex-direction: column; gap: var(--app-space-2); }
.provider { display: flex; align-items: center; justify-content: space-between; gap: var(--app-space-4); flex-wrap: wrap; padding: var(--app-space-3); border-radius: var(--md-sys-shape-corner-medium); background: var(--md-sys-color-surface-container); }
.provider-text { display: flex; flex-direction: column; gap: var(--app-space-1); min-width: 0; flex: 1; }
.provider-name { display: flex; align-items: center; gap: var(--app-space-2); flex-wrap: wrap; }
.name { min-width: 0; overflow-wrap: anywhere; }
@media (max-width: 599px) {
  .settings { padding: var(--app-space-3); }
  .section { padding: var(--app-space-4); }
}
</style>
