<script setup lang="ts">
import ActionButton from '@/components/chat/ActionButton.vue';
import { useI18n } from '@/i18n';
import { icons } from '@/ui';

/** "‹ 2 / 3 ›": which version of a turn is on screen (edits of a question, retries of a reply), and the way to the others. */
defineProps<{ index: number; total: number; disabled?: boolean }>();
const emit = defineEmits<{ previous: []; next: [] }>();
const { t } = useI18n();
</script>

<template>
  <div class="versions" role="group" :aria-label="t('playground.version', { n: index + 1, total })">
    <ActionButton :icon="icons.ChevronLeft" :label="t('playground.previous')" :disabled="disabled || index === 0" @click="emit('previous')" />
    <span class="count type-label-medium num" aria-hidden="true">{{ t('playground.version', { n: index + 1, total }) }}</span>
    <ActionButton :icon="icons.ChevronRight" :label="t('playground.next')" :disabled="disabled || index >= total - 1" @click="emit('next')" />
  </div>
</template>

<style scoped>
.versions { display: inline-flex; align-items: center; color: var(--md-sys-color-on-surface-variant); }
.count { min-width: 3ch; text-align: center; }
</style>
