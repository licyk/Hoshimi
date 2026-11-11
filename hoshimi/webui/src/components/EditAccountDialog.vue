<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue';
import { accountFromForm, AccountFormError, type CredentialForm } from '@/api/accountForms';
import { getAccount, useUpdateAccount } from '@/api/queries/channels';
import { useI18n } from '@/i18n';
import { AppButton, AppDialog, ErrorNotice, IconButton, SelectField, TextField, icons, useSnackbar } from '@/ui';

const props = defineProps<{ provider: string; accountId: string }>();
const open = defineModel<boolean>('open', { default: false });
const { t } = useI18n();
const snackbar = useSnackbar();
const update = useUpdateAccount();
const form = ref<CredentialForm>();
const values = ref<Record<string, string>>({});
const original = ref<Record<string, unknown>>({});
const revealed = ref<Record<string, boolean>>({});
const loading = ref(false);
const error = ref('');
let generation = 0;
const stringify = (value: unknown) => value === null || value === undefined ? '' : typeof value === 'object' ? JSON.stringify(value) : String(value);
const secret = (field: CredentialForm['fields'][number]) => field.kind === 'secret' || /token|secret|password|key|cookie|credential/i.test(field.key);
const label = (key: string) => {
  const translated = t(`channels.fields.${key}`);
  return translated === `channels.fields.${key}` ? key : translated;
};
const canSave = computed(() => !!form.value && !loading.value && !update.isPending.value && form.value.fields.every((f) => !f.required || values.value[f.key]?.trim()));

function reset() {
  generation++;
  form.value = undefined;
  values.value = {};
  original.value = {};
  revealed.value = {};
  error.value = '';
  loading.value = false;
  update.reset();
}

async function load() {
  reset();
  const mine = generation;
  loading.value = true;
  try {
    const detail = await getAccount(props.provider, props.accountId);
    if (mine !== generation || !open.value) return;
    form.value = detail.form;
    original.value = detail.account;
    values.value = Object.fromEntries(detail.form.fields.map((f) => [f.key, stringify(detail.account[f.key] ?? detail.form.defaults[f.key])]));
  } catch (e) {
    if (mine === generation) error.value = (e as Error).message;
  } finally {
    if (mine === generation) loading.value = false;
  }
}
watch(() => [open.value, props.provider, props.accountId], () => open.value ? void load() : reset(), { immediate: true });
onBeforeUnmount(reset);

function submit() {
  if (!canSave.value || !form.value) return;
  let account: Record<string, unknown>;
  try {
    account = accountFromForm(form.value, values.value);
    // Retain types and exact spelling of values the user did not change.
    for (const field of form.value.fields) {
      if (field.key in original.value && values.value[field.key] === stringify(original.value[field.key])) account[field.key] = original.value[field.key];
    }
  } catch (e) {
    error.value = e instanceof AccountFormError
      ? t(e.reason === 'json' ? 'channels.invalidJson' : e.reason === 'choice' ? 'channels.invalidChoice' : 'channels.fieldRequired', { field: label(e.field) })
      : (e as Error).message;
    return;
  }
  error.value = '';
  const mine = generation;
  update.mutate({ provider: props.provider, id: props.accountId, account }, {
    onSuccess: () => {
      if (mine !== generation) return;
      open.value = false;
      snackbar.show(t('common.saved'));
    },
    onError: (e) => { if (mine === generation) error.value = e.message; },
  });
}
</script>

<template>
  <AppDialog v-model:open="open" :title="t('channels.editTitle', { id: accountId })" width="medium">
    <form class="edit-account" @submit.prevent="submit">
      <p v-if="loading" class="type-body-medium muted" role="status">{{ t('common.loading') }}</p>
      <template v-for="field in form?.fields ?? []" :key="field.key">
        <SelectField v-if="field.kind === 'select'" v-model="values[field.key]" :label="label(field.key)" :disabled="update.isPending.value"
          :options="(field.choices ?? []).map((value) => ({ value, label: t(`channels.protocols.${value}`) }))" />
        <TextField v-else v-model="values[field.key]" :label="label(field.key)" autocomplete="off" :disabled="update.isPending.value"
          :type="secret(field) && !revealed[field.key] ? 'password' : field.kind === 'json' ? 'textarea' : 'text'"
          :supporting-text="field.kind === 'list' ? t('channels.modelListHelp') : undefined">
          <template v-if="secret(field)" #trailing>
            <IconButton :icon="revealed[field.key] ? icons.EyeOff : icons.Eye" :disabled="update.isPending.value"
              :label="t(revealed[field.key] ? 'channels.hideSecret' : 'channels.showSecret', { field: label(field.key) })"
              :pressed="!!revealed[field.key]" @click="revealed[field.key] = !revealed[field.key]" />
          </template>
        </TextField>
      </template>
      <ErrorNotice v-if="error" :message="error" announcement="assertive">
        <template v-if="!form" #actions><AppButton variant="text" @click="load">{{ t('common.retry') }}</AppButton></template>
      </ErrorNotice>
    </form>
    <template #actions>
      <AppButton variant="text" :disabled="update.isPending.value" @click="open = false">{{ t('common.cancel') }}</AppButton>
      <AppButton variant="filled" :loading="update.isPending.value" :disabled="!canSave" @click="submit">{{ t('common.save') }}</AppButton>
    </template>
  </AppDialog>
</template>

<style scoped>
.edit-account { display: flex; flex-direction: column; gap: var(--app-space-3); }
p { margin: 0; }
</style>
