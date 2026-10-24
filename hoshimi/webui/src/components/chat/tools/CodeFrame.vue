<script setup lang="ts">
import { onBeforeUnmount, ref, type Component } from 'vue';
import { useI18n } from '@/i18n';
import { AppIcon, icons } from '@/ui';
import '@/ui/codeBlock.css';

/**
 * What a tool call asked or said, in the frame a reply's code blocks have (``codeBlock.css``): a
 * toolbar with an icon and what the frame holds (a file, a command, its output) and a word after it,
 * short facts (``tags``: an exit code, the lines a change adds and removes), its buttons (``tools``)
 * and copy last, when there is something to copy; under it, what it holds.
 */
const props = defineProps<{ icon: Component; title: string; sub?: string; copy?: string; path?: boolean }>();
const { t } = useI18n();
const copied = ref(false);
let timer: ReturnType<typeof setTimeout> | undefined;
onBeforeUnmount(() => clearTimeout(timer));

async function onCopy() {
  try {
    await navigator.clipboard.writeText(props.copy ?? '');
  } catch {
    return;
  }
  copied.value = true;
  clearTimeout(timer);
  timer = setTimeout(() => (copied.value = false), 1500);
}
</script>

<template>
  <div class="code-block tool-frame">
    <div class="code-head">
      <span class="code-title">
        <AppIcon :icon="icon" :size="18" class="code-icon" />
        <span class="code-lang" :class="{ 'code-path': path }" :title="title">{{ title }}</span>
        <span v-if="sub" class="code-sub">{{ sub }}</span>
      </span>
      <span v-if="$slots.tags" class="code-tags"><slot name="tags" /></span>
      <span class="code-tools">
        <slot name="tools" />
        <button
          v-if="copy !== undefined"
          type="button"
          class="code-copy"
          :class="{ copied }"
          :aria-label="copied ? t('playground.codeCopied') : t('playground.copyCode')"
          :title="copied ? t('playground.codeCopied') : t('playground.copyCode')"
          @click="onCopy"
        >
          <AppIcon :icon="copied ? icons.Check : icons.Copy" :size="18" />
        </button>
      </span>
    </div>
    <slot />
  </div>
</template>
