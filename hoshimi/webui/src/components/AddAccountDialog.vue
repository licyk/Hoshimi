<script setup lang="ts">
import { computed, ref, watch } from 'vue';
import { accountFromForm, AccountFormError } from '@/api/accountForms';
import { useAddAccount, useProviderForms, useStations } from '@/api/queries/channels';
import { useI18n } from '@/i18n';
import { AppButton, AppDialog, PickerMenu, SegmentedButton, SelectField, TextField, icons, useSnackbar } from '@/ui';

/**
 * Add one credential to a channel. The aggregator lane (``agg``) names a station from a long
 * searchable list, or its own address; every other lane fills the form its server publishes, one
 * per authentication method. Whatever closes the dialog clears it, so a key never sits in a form
 * the operator cannot see.
 */
const props = defineProps<{ provider: string; name: string }>();
const open = defineModel<boolean>('open', { default: false });
const { t } = useI18n();
const snackbar = useSnackbar();
const stations = useStations();
const providerForms = useProviderForms();
const add = useAddAccount();

/** A value no station id can hold, so "paste the address yourself" is a real choice. */
const CUSTOM_STATION = '__custom__';
const station = ref<string | null>(CUSTOM_STATION);
const baseUrl = ref('');
const apiKey = ref('');
const accountId = ref('');
const error = ref('');
const authMethod = ref<string | null>(null);
const values = ref<Record<string, string | number | null>>({});

const aggregator = computed(() => props.provider === 'agg');
const forms = computed(() => providerForms.data.value?.find((entry) => entry.id === props.provider)?.forms ?? []);
const form = computed(() => forms.value.find((f) => f.method === authMethod.value) ?? forms.value[0]);
// The segmented control holds a method, never nothing: the first form's until one is chosen.
const method = computed({ get: () => authMethod.value ?? forms.value[0]?.method ?? '', set: (v: string | null) => (authMethod.value = v) });
const methodOptions = computed(() => forms.value.map((f) => ({ value: f.method, label: t(`channels.methods.${f.method}`) })));
const chosen = computed(() => stations.data.value?.find((s) => s.id === station.value) ?? null);
const stationOptions = computed(() => [
  { value: CUSTOM_STATION, label: t('channels.addCustom'), description: t('channels.addStationHelp') },
  // The station's own address and the variable it reads its key from: facts, so not translated.
  ...(stations.data.value ?? []).map((s) => ({ value: s.id, label: s.display_name, description: `${s.base_url} · ${s.key_env}`, keywords: [s.id, s.base_url] })),
]);
const canAdd = computed(() =>
  aggregator.value
    ? !!apiKey.value.trim() && (!!chosen.value || !!baseUrl.value.trim())
    : !!form.value && form.value.fields.every((field) => !field.required || String(values.value[field.key] ?? '').trim()),
);

watch(authMethod, () => (values.value = { ...form.value?.defaults }));
watch(open, (value) => {
  if (!value) return;
  authMethod.value = forms.value[0]?.method ?? null;
  values.value = { ...form.value?.defaults };
  error.value = '';
});

function reset() {
  station.value = CUSTOM_STATION;
  baseUrl.value = '';
  apiKey.value = '';
  accountId.value = '';
  error.value = '';
  values.value = {};
}

const fieldLabel = (key: string) => t(`channels.fields.${props.provider === 'eac' && key === 'base_url' ? 'relay_base_url' : key}`);
const fieldHelp = (kind: string, key: string) =>
  kind === 'list' ? t('channels.modelListHelp') : key === 'base_url' ? t(props.provider === 'eac' ? 'channels.eacRelayHelp' : 'channels.baseUrlHelp') : kind === 'secret' ? t('channels.addKeyHelp') : undefined;

function submit() {
  let account: Record<string, unknown>;
  if (aggregator.value) {
    account = { api_key: apiKey.value.trim() };
    if (chosen.value) account.preset = chosen.value.id;
    else account.base_url = baseUrl.value.trim();
  } else {
    if (!form.value) return;
    try {
      account = accountFromForm(form.value, values.value);
    } catch (e) {
      error.value =
        e instanceof AccountFormError
          ? t(e.reason === 'json' ? 'channels.invalidJson' : e.reason === 'choice' ? 'channels.invalidChoice' : 'channels.fieldRequired', { field: t(`channels.fields.${e.field}`) })
          : t('common.error');
      return;
    }
  }
  const wanted = accountId.value.trim();
  if (wanted) account.id = wanted;
  error.value = '';
  add.mutate(
    { provider: props.provider, account },
    {
      onSuccess: (row) => {
        open.value = false;
        snackbar.show(t('channels.accountAdded', { id: row.account }));
      },
      onError: (e) => (error.value = e.message || t('common.error')),
    },
  );
}
</script>

<template>
  <AppDialog v-model:open="open" :title="t('channels.addTitle', { name })" width="medium" @closed="reset">
    <form class="add" @submit.prevent="canAdd && submit()">
      <template v-if="aggregator">
        <PickerMenu
          v-model="station"
          :label="t('channels.addStation')"
          :options="stationOptions"
          :loading="stations.isPending.value"
          :search-label="t('channels.searchStations')"
          :search-from="8"
          :no-matches="t('channels.noStations')"
        />
        <TextField v-if="!chosen" v-model="baseUrl" type="url" :label="t('channels.addBaseUrl')" placeholder="https://openrouter.ai/api/v1" :supporting-text="t('channels.addBaseUrlHelp')" />
        <TextField v-model="apiKey" type="password" autocomplete="off" :label="t('channels.addKey')" :icon="icons.KeyRound" :supporting-text="t('channels.addKeyHelp')" />
      </template>
      <template v-else>
        <div v-if="forms.length > 1" class="method">
          <span class="type-label-large muted">{{ t('channels.authMethod') }}</span>
          <SelectField v-if="forms.length > 3" v-model="method" :options="methodOptions" :label="t('channels.authMethod')" />
          <SegmentedButton v-else v-model="method" :options="methodOptions" />
        </div>
        <p v-if="provider === 'eac' && authMethod === 'bundled'" class="type-body-small muted">{{ t('channels.eacBundledHelp') }}</p>
        <template v-for="field in form?.fields ?? []" :key="`${authMethod}:${field.key}`">
          <SelectField
            v-if="field.kind === 'select'"
            :model-value="String(values[field.key] ?? '')"
            :label="t(`channels.fields.${field.key}`)"
            :options="(field.choices ?? []).map((value) => ({ value, label: t(`channels.protocols.${value}`) }))"
            @update:model-value="values[field.key] = $event"
          />
          <TextField
            v-else
            v-model="values[field.key]"
            :type="field.kind === 'secret' ? 'password' : 'text'"
            autocomplete="off"
            :label="fieldLabel(field.key)"
            :supporting-text="fieldHelp(field.kind, field.key)"
          />
        </template>
      </template>
      <TextField v-model="accountId" :label="t('channels.addAccountId')" :supporting-text="t('channels.addAccountIdHelp')" />
      <p v-if="error" class="type-body-medium error-text" role="alert">{{ error }}</p>
    </form>
    <template #actions>
      <AppButton variant="text" :disabled="add.isPending.value" @click="open = false">{{ t('common.cancel') }}</AppButton>
      <AppButton variant="filled" :loading="add.isPending.value" :disabled="!canAdd" @click="submit">{{ t('channels.addSave') }}</AppButton>
    </template>
  </AppDialog>
</template>

<style scoped>
.add { display: flex; flex-direction: column; gap: var(--app-space-3); }
.method { display: flex; flex-direction: column; gap: var(--app-space-2); align-items: flex-start; }
p { margin: 0; }
.error-text { color: var(--md-sys-color-error); }
</style>
