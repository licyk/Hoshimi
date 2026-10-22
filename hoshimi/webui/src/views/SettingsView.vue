<script setup lang="ts">
import { computed } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import { BASE_URL } from '@/api/baseUrl';
import { useMeta, useModelSpecs, useModels, useSettings, useUpdateSettings, useVersion } from '@/api/queries/app';
import { useChannels } from '@/api/queries/channels';
import { TOOL_GROUPS, type ToolGroup } from '@/api/types';
import NetworkSettings from '@/components/NetworkSettings.vue';
import { useI18n } from '@/i18n';
import { usePreferencesStore } from '@/stores/preferences';
import { AppButton, AppIcon, AxisPanes, ChipInput, Collapse, DataList, NumberField, PageSection, PickerMenu, SegmentedButton, SelectField, Skeleton, Switch, Tabs, TextField, TokenField, icons, useSnackbar } from '@/ui';

/**
 * Settings in tabs, one concern each, sliding along the shared axis as the tab changes; the tab
 * is in the address (``?tab=``). Every field saves on its own as it changes. A channel's own
 * switch and network route are on its page in Channels, not here.
 */
type Tab = 'general' | 'tools' | 'access' | 'security' | 'network' | 'automation' | 'appearance' | 'about';
const TABS = ['general', 'tools', 'access', 'security', 'network', 'automation', 'appearance', 'about'] as const;
const { t, localeOptions } = useI18n();
const route = useRoute();
const router = useRouter();
const settings = useSettings();
const update = useUpdateSettings();
const version = useVersion();
const meta = useMeta();
const models = useModels();
const specs = useModelSpecs();
const channels = useChannels();
const snackbar = useSnackbar();
const prefs = usePreferencesStore();
const s = computed(() => settings.data.value);
const listeningAddress = computed(() => {
  const l = s.value?.listening;
  return l ? `${l.host.includes(':') ? `[${l.host}]` : l.host}:${l.port}` : '';
});

const tab = computed<Tab>({
  get: () => (TABS.includes(route.query.tab as Tab) ? (route.query.tab as Tab) : 'general'),
  set: (value) => void router.replace({ query: { ...route.query, tab: value } }),
});
const tabs = computed(() => TABS.map((v) => ({ value: v, label: t(`settings.sections.${v}`) })));

/** Save one patch; each change saves on its own with a snackbar confirmation. */
function save(patch: Record<string, unknown>, restart = false) {
  update.mutate(patch, {
    onSuccess: () => snackbar.show(restart ? `${t('common.saved')} · ${t('common.restartNeeded')}` : t('common.saved')),
    onError: (e) => snackbar.error((e as Error).message),
  });
}
const num = (v: string | number | null) => (v === '' || v === null ? null : Number(v));
const MIB = 1024 * 1024;
/** The arrows' names on a number field, from the field's own label. */
const arrows = (label: string) => ({ increaseLabel: t('common.increase', { name: t(label) }), decreaseLabel: t('common.decrease', { name: t(label) }) });

// General
const NO_MODEL = '';
const modelOptions = computed(() => [
  { value: NO_MODEL, label: t('settings.noDefaultModel'), description: t('settings.noDefaultModelHelp') },
  ...(models.data.value?.data ?? []).map((m) => ({ value: m.id, label: m.display_name || m.id, description: m.display_name && m.display_name !== m.id ? `${m.id} · ${m.owned_by}` : m.owned_by })),
]);
const defaultModel = computed({
  get: () => s.value?.server.default_model ?? NO_MODEL,
  set: (value: string | null) => save({ server: { default_model: value || null } }),
});
// Playground: the models that title chats and describe images; a vision model is offered only if it reads images.
const catalogOptions = computed(() => modelOptions.value.filter((o) => o.value !== NO_MODEL));
const titleModelOptions = computed(() => [{ value: '', label: t('settings.titleModelSame') }, ...catalogOptions.value]);
const visionModelOptions = computed(() => {
  const seeing = new Set((specs.data.value?.data ?? []).filter((m) => m.vision).map((m) => m.id));
  return [
    { value: 'auto', label: t('settings.visionAuto'), description: t('settings.visionAutoHelp') },
    { value: 'off', label: t('settings.visionOff'), description: t('settings.visionOffHelp') },
    ...catalogOptions.value.filter((o) => seeing.has(o.value)),
  ];
});
const titleModel = computed({
  get: () => s.value?.playground.title_model ?? '',
  set: (value: string | null) => save({ playground: { title_model: value ?? '' } }),
});
const visionModel = computed({
  get: () => s.value?.playground.vision_model || 'auto',
  set: (value: string | null) => save({ playground: { vision_model: value || 'auto' } }),
});
// Tools: each switch saves the whole list.
function setTool(group: ToolGroup, on: boolean) {
  const current = s.value?.playground.tools ?? [];
  save({ playground: { tools: on ? TOOL_GROUPS.filter((g) => g === group || current.includes(g)) : current.filter((g) => g !== group) } });
}
const locale = computed({ get: () => prefs.prefs.locale, set: (v: string | null) => (prefs.prefs.locale = (v ?? 'auto') as typeof prefs.prefs.locale) });

// Automation
const checkin = computed(() => channels.data.value?.checkin);
const checkinLine = computed(() =>
  checkin.value?.last_date ? `${t('channels.checkedAt', { when: new Date(checkin.value.last_at * 1000).toLocaleString() })}${checkin.value.last_result ? ` · ${checkin.value.last_result}` : ''}` : t('channels.notChecked'),
);

// Appearance
const themes = computed(() => [
  { value: 'light' as const, icon: icons.Sun, label: t('settings.themes.light') },
  { value: 'dark' as const, icon: icons.Moon, label: t('settings.themes.dark') },
  { value: 'system' as const, icon: icons.SunMoon, label: t('settings.themes.system') },
]);
const contrasts = computed(() => [
  { value: '0', label: t('settings.contrastLevels.standard') },
  { value: '0.5', label: t('settings.contrastLevels.medium') },
  { value: '1', label: t('settings.contrastLevels.high') },
]);
const contrast = computed({ get: () => String(prefs.prefs.contrast), set: (v: string) => (prefs.prefs.contrast = Number(v)) });
const motions = computed(() => (['system', 'reduced'] as const).map((v) => ({ value: v, label: t(`settings.motions.${v}`) })));

// About
const openDocs = () => window.open(`${BASE_URL}/docs`, '_blank', 'noopener');
async function copy(text: string) {
  try {
    await navigator.clipboard.writeText(text);
    snackbar.show(t('common.copied'));
  } catch {
    snackbar.error(t('common.error'));
  }
}
</script>

<template>
  <div class="settings">
    <Tabs v-model="tab" :tabs="tabs" />
    <Collapse :show="!!s?.restart_required">
      <div v-if="s" class="banner" role="status">
        <AppIcon :icon="icons.AlertTriangle" :size="20" />
        <span class="type-body-medium">{{ t('settings.restartBanner', { host: s.server.host, port: s.server.port }) }}</span>
      </div>
    </Collapse>
    <div v-if="!s" class="skeletons"><Skeleton v-for="i in 3" :key="i" height="96px" shape="medium" /></div>
    <AxisPanes v-else :value="tab" :order="TABS">
      <div v-if="tab === 'general'" key="general" class="panel">
        <PageSection :title="t('settings.sections.general')" :subtitle="t('settings.generalHint')">
          <SelectField v-model="locale" :label="t('settings.language')" :options="localeOptions" />
          <PickerMenu
            v-model="defaultModel"
            :label="t('settings.defaultModel')"
            :options="modelOptions"
            :loading="models.isPending.value"
            :search-label="t('settings.searchModels')"
            :search-from="8"
            :no-matches="t('models.none')"
          />
          <p class="type-body-small muted">{{ t('settings.defaultModelHelp') }}</p>
        </PageSection>
        <PageSection :title="t('settings.requests')" :subtitle="t('settings.requestsHint')">
          <div class="row">
            <NumberField :model-value="s.server.max_account_retries" :min="1" :label="t('settings.maxAccountRetries')" :supporting-text="t('settings.maxAccountRetriesHelp')" v-bind="arrows('settings.maxAccountRetries')" @change="save({ server: { max_account_retries: num($event) } })" />
            <!-- Stepped by the mebibyte: a byte at a time would take a million clicks to matter. -->
            <NumberField :model-value="s.server.max_body_bytes" :min="1" :step="MIB" :label="t('settings.maxBodyBytes')" :supporting-text="t('settings.maxBodyBytesHelp')" v-bind="arrows('settings.maxBodyBytes')" @change="save({ server: { max_body_bytes: num($event) } })" />
          </div>
        </PageSection>
        <PageSection :title="t('settings.history')" :subtitle="t('settings.historyHint')">
          <div class="row">
            <NumberField :model-value="s.server.stats_keep_days" :min="1" :label="t('settings.keepDays')" :supporting-text="t('settings.keepDaysHelp')" v-bind="arrows('settings.keepDays')" @change="save({ server: { stats_keep_days: num($event) } })" />
          </div>
        </PageSection>
        <PageSection :title="t('settings.playground')" :subtitle="t('settings.playgroundHint')">
          <TextField
            :model-value="s.playground.system_prompt"
            type="textarea"
            :rows="4"
            :label="t('settings.systemPrompt')"
            :supporting-text="t('settings.systemPromptHelp')"
            @change="save({ playground: { system_prompt: $event } })"
          />
          <div class="row">
            <div class="field">
              <PickerMenu v-model="titleModel" :label="t('settings.titleModel')" :options="titleModelOptions" :loading="models.isPending.value" :search-label="t('settings.searchModels')" :search-from="8" :no-matches="t('models.none')" />
              <p class="type-body-small muted">{{ t('settings.titleModelHelp') }}</p>
            </div>
            <div class="field">
              <PickerMenu v-model="visionModel" :label="t('settings.visionModel')" :options="visionModelOptions" :loading="models.isPending.value" :search-label="t('settings.searchModels')" :search-from="8" :no-matches="t('models.none')" />
              <p class="type-body-small muted">{{ t('settings.visionModelHelp') }}</p>
            </div>
          </div>
        </PageSection>
      </div>

      <div v-else-if="tab === 'tools'" key="tools" class="panel">
        <PageSection :title="t('settings.tools')" :subtitle="t('settings.toolsHint')">
          <p class="type-body-medium tools-warning" role="note"><AppIcon :icon="icons.ShieldAlert" :size="18" />{{ t('settings.toolsWarning') }}</p>
          <Switch
            v-for="g in TOOL_GROUPS"
            :key="g"
            :model-value="s.playground.tools.includes(g)"
            :label="t(`playground.toolNames.${g}`)"
            :supporting-text="t(`settings.toolHelp.${g}`)"
            @update:model-value="setTool(g, $event)"
          />
        </PageSection>
        <PageSection :title="t('settings.toolsRun')" :subtitle="t('settings.toolsRunHint')">
          <Switch
            :model-value="s.playground.approval === 'ask'"
            :label="t('settings.approval')"
            :supporting-text="t('settings.approvalHelp')"
            @update:model-value="save({ playground: { approval: $event ? 'ask' : 'auto' } })"
          />
          <TextField
            :model-value="s.playground.workspace"
            :label="t('settings.workspace')"
            :placeholder="s.playground.workspace_path"
            :supporting-text="t('settings.workspaceHelp', { path: s.playground.workspace_path })"
            @change="save({ playground: { workspace: $event } })"
          />
          <div class="row">
            <NumberField :model-value="s.playground.max_steps" :min="1" :max="500" :label="t('settings.maxSteps')" :supporting-text="t('settings.maxStepsHelp')" v-bind="arrows('settings.maxSteps')" @change="save({ playground: { max_steps: num($event) } })" />
            <TextField :model-value="s.playground.search_url" :label="t('settings.searchUrl')" placeholder="https://searx.example.org" :supporting-text="t('settings.searchUrlHelp')" @change="save({ playground: { search_url: $event } })" />
          </div>
        </PageSection>
      </div>

      <div v-else-if="tab === 'access'" key="access" class="panel">
        <PageSection :title="t('settings.listen')" :subtitle="t('common.restartNeeded')">
          <div class="row">
            <TextField :model-value="s.server.host" :label="t('settings.host')" @change="save({ server: { host: $event } }, true)" />
            <NumberField :model-value="s.server.port" :min="0" :max="65535" :label="t('settings.port')" v-bind="arrows('settings.port')" @change="save({ server: { port: num($event) } }, true)" />
          </div>
          <p v-if="s.listening" class="type-body-medium listening" :class="{ fallback: s.listening.fallback }" role="status">
            <AppIcon :icon="s.listening.fallback ? icons.AlertTriangle : icons.Radio" :size="18" />
            <span>
              {{ t('settings.listeningOn', { address: listeningAddress }) }}
              <template v-if="s.listening.fallback"> · {{ t('settings.listeningFallback', { requested: s.listening.requested_port, port: s.listening.port }) }}</template>
            </span>
          </p>
          <Switch :model-value="s.server.strict_port" :label="t('settings.strictPort')" :supporting-text="t('settings.strictPortHelp')" @update:model-value="save({ server: { strict_port: $event } }, true)" />
        </PageSection>
        <PageSection :title="t('settings.keys')" :subtitle="t('settings.keysHint')">
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
        </PageSection>
      </div>

      <div v-else-if="tab === 'security'" key="security" class="panel">
        <PageSection :title="t('settings.sections.security')" :subtitle="t('settings.securityHint')">
          <ChipInput :label="t('settings.allowCidrs')" :values="s.server.allow_cidrs" placeholder="10.0.0.0/8" :supporting-text="t('settings.allowCidrsHelp')" :remove-label="(v) => t('common.removeValue', { value: v })" @change="save({ security: { allow_cidrs: $event } })" />
          <ChipInput :label="t('settings.allowHosts')" :values="s.server.allow_hosts" placeholder="gateway.example.com" :supporting-text="t('settings.allowHostsHelp')" :remove-label="(v) => t('common.removeValue', { value: v })" @change="save({ security: { allow_hosts: $event } })" />
          <ChipInput :label="t('settings.allowOrigins')" :values="s.server.allow_origins" placeholder="https://app.example.com" :supporting-text="t('settings.allowOriginsHelp')" :remove-label="(v) => t('common.removeValue', { value: v })" @change="save({ security: { allow_origins: $event } })" />
        </PageSection>
      </div>

      <div v-else-if="tab === 'network'" key="network" class="panel">
        <PageSection :title="t('network.title')" :subtitle="t('settings.networkHint')">
          <NetworkSettings :value="s.network" @save="save({ network: $event })" />
          <AppButton variant="text" :icon="icons.Fingerprint" class="start" @click="router.push('/channels')">{{ t('settings.perChannelNetwork') }}</AppButton>
        </PageSection>
      </div>

      <div v-else-if="tab === 'automation'" key="automation" class="panel">
        <PageSection :title="t('settings.sections.automation')" :subtitle="t('settings.automationHint')">
          <Switch :model-value="s.server.auto_checkin" :label="t('channels.autoCheckin')" :supporting-text="t('channels.autoCheckinHelp')" @update:model-value="save({ server: { auto_checkin: $event } })" />
          <p class="type-body-small muted status">{{ t('channels.todayCheckin') }}: {{ checkinLine }}</p>
          <Switch :model-value="s.server.auto_refresh" :label="t('channels.autoRefresh')" :supporting-text="t('channels.autoRefreshHelp')" @update:model-value="save({ server: { auto_refresh: $event } })" />
        </PageSection>
      </div>

      <div v-else-if="tab === 'appearance'" key="appearance" class="panel">
        <PageSection :title="t('settings.sections.appearance')">
          <div class="field-row">
            <span class="type-body-large">{{ t('settings.theme') }}</span>
            <SegmentedButton v-model="prefs.prefs.theme" :options="themes" />
          </div>
          <label class="field-row">
            <span class="type-body-large">{{ t('settings.sourceColor') }}</span>
            <input v-model="prefs.prefs.sourceColor" type="color" class="color" :aria-label="t('settings.sourceColor')" />
          </label>
          <div class="field-row">
            <span class="type-body-large">{{ t('settings.contrast') }}</span>
            <SegmentedButton v-model="contrast" :options="contrasts" />
          </div>
          <div class="field-row">
            <span class="field-text">
              <span class="type-body-large">{{ t('settings.motion') }}</span>
              <span class="type-body-small muted">{{ t('settings.motionHelp') }}</span>
            </span>
            <SegmentedButton v-model="prefs.prefs.motion" :options="motions" />
          </div>
        </PageSection>
      </div>

      <div v-else key="about" class="panel">
        <PageSection :title="t('settings.sections.about')">
          <DataList
            :rows="[
              { label: t('settings.version'), value: version.data.value?.version ?? '—' },
              { label: t('settings.dataDir'), value: s.data_dir, mono: true },
              { label: t('settings.configPath'), value: s.config_path, mono: true },
              { label: t('settings.database'), value: `${s.data_dir}${s.data_dir.includes('\\') ? '\\' : '/'}hoshimi.db`, mono: true },
            ]"
          />
          <div class="buttons">
            <AppButton variant="tonal" :icon="icons.Copy" @click="copy(s.config_path)">{{ t('settings.copyConfigPath') }}</AppButton>
            <AppButton v-if="meta.data.value && !meta.data.value.auth_required" variant="text" :icon="icons.ExternalLink" @click="openDocs">{{ t('overview.apiDocs') }}</AppButton>
          </div>
        </PageSection>
      </div>
    </AxisPanes>
  </div>
</template>

<style scoped>
.settings { display: flex; flex-direction: column; gap: var(--app-space-4); padding: var(--app-space-4) var(--app-space-6) var(--app-space-8); padding-inline-end: calc(var(--app-space-6) - var(--app-scroll-lane)); min-width: 0; }
.skeletons { display: flex; flex-direction: column; gap: var(--app-space-3); }
.panel { display: flex; flex-direction: column; gap: var(--app-space-4); min-width: 0; }
.banner {
  display: flex; align-items: center; gap: var(--app-space-3); padding: var(--app-space-3) var(--app-space-4);
  border-radius: var(--md-sys-shape-corner-medium); background: var(--md-sys-color-tertiary-container); color: var(--md-sys-color-on-tertiary-container);
}
p { margin: 0; }
.listening { display: flex; align-items: center; gap: var(--app-space-2); margin: 0; color: var(--md-sys-color-on-surface-variant); }
.listening.fallback { color: var(--md-sys-color-tertiary); }
.row { display: flex; flex-wrap: wrap; gap: var(--app-space-3); align-items: flex-start; }
.row > * { flex: 1 1 260px; min-width: 0; }
.field { display: flex; flex-direction: column; gap: var(--app-space-1); }
.tools-warning {
  display: flex; align-items: flex-start; gap: var(--app-space-2); margin: 0; padding: var(--app-space-3);
  border-radius: var(--md-sys-shape-corner-medium); background: var(--md-sys-color-tertiary-container); color: var(--md-sys-color-on-tertiary-container);
}
.tools-warning :deep(svg) { flex: none; margin-top: 2px; }
.field p { margin: 0; }
.field-row { display: flex; align-items: center; justify-content: space-between; gap: var(--app-space-4); min-height: 56px; flex-wrap: wrap; }
.field-text { display: flex; flex-direction: column; min-width: 0; flex: 1 1 200px; }
.color { width: 56px; height: 40px; padding: 0; border: 1px solid var(--md-sys-color-outline); border-radius: var(--md-sys-shape-corner-small); background: transparent; cursor: pointer; }
.status { padding-left: var(--app-space-1); }
.buttons { display: flex; flex-wrap: wrap; gap: var(--app-space-2); }
.start { align-self: flex-start; }
@container app-content (max-width: 599px) {
  .settings { padding: var(--app-space-3); padding-inline-end: max(var(--app-space-1), calc(var(--app-space-3) - var(--app-scroll-lane))); }
}
</style>
