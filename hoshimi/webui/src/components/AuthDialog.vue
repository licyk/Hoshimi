<script setup lang="ts">
import { useQueryClient } from '@tanstack/vue-query';
import { computed, onBeforeUnmount, ref, watch } from 'vue';
import { BASE_URL } from '@/api/baseUrl';
import { useI18n } from '@/i18n';
import { useAuthStore } from '@/stores/auth';
import { AppButton, AppDialog, TextField, icons } from '@/ui';

/** Asks for the access token when the server answers 401. */
const auth = useAuthStore();
const qc = useQueryClient();
const { t } = useI18n();
const value = ref('');
const pending = ref(false);
const error = ref('');
const open = computed({ get: () => auth.needed, set: (v) => (auth.needed = v) });
let active: AbortController | null = null;
watch(() => auth.needed, (needed) => {
  error.value = '';
  if (!needed) {
    active?.abort();
    value.value = '';
  }
}, { flush: 'sync' });
onBeforeUnmount(() => active?.abort());

async function submit() {
  const token = value.value.trim();
  if (!token || pending.value) return;
  pending.value = true;
  error.value = '';
  const controller = new AbortController();
  active = controller;
  try {
    const response = await fetch(`${BASE_URL}/api/v1/app/meta`, { headers: { Authorization: `Bearer ${token}` }, signal: controller.signal });
    if (!auth.needed || controller.signal.aborted) return;
    if (!response.ok) {
      error.value = t(response.status === 401 ? 'auth.invalid' : 'auth.failed');
      return;
    }
    auth.setToken(token);
    value.value = '';
    qc.invalidateQueries();
  } catch {
    if (!controller.signal.aborted) error.value = t('auth.failed');
  } finally {
    active = null;
    pending.value = false;
  }
}
</script>

<template>
  <AppDialog v-model:open="open" :title="t('auth.title')" width="small">
    <p class="type-body-medium muted">{{ t('auth.text') }}</p>
    <TextField v-model="value" type="password" :label="t('settings.accessToken')" :icon="icons.KeyRound" autocomplete="current-password" :disabled="pending" :error-text="error" @enter="submit" />
    <template #actions>
      <AppButton :disabled="!value.trim()" :loading="pending" @click="submit">{{ t('auth.submit') }}</AppButton>
    </template>
  </AppDialog>
</template>
