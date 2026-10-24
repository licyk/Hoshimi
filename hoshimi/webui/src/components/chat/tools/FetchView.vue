<script setup lang="ts">
import { computed, ref } from 'vue';
import CodeFrame from '@/components/chat/tools/CodeFrame.vue';
import RawOutput from '@/components/chat/tools/RawOutput.vue';
import { hostOf, parseFetch } from '@/components/chat/tools/parse';
import { useI18n } from '@/i18n';
import { AppIcon, icons } from '@/ui';
// Not through '@/ui': markdown-it and highlight.js load with the Playground alone.
import Markdown from '@/ui/Markdown.vue';

/**
 * A page ``web_fetch`` read: its title and site, a link to it, whether the tool cut it, and its text
 * as the Markdown the tool made of it, in a box that opens to its whole length when asked.
 */
const props = defineProps<{ output: string; failed: boolean }>();
const { t } = useI18n();
const page = computed(() => (props.failed ? null : parseFetch(props.output)));
const whole = ref(false);
</script>

<template>
  <CodeFrame v-if="page" :icon="icons.Globe" :title="page.title || hostOf(page.url)" :sub="page.title ? hostOf(page.url) : ''" :copy="page.body">
    <template v-if="page.cut" #tags>
      <span class="code-tag warn" :title="t('playground.toolView.truncated', { at: page.cut.at, of: page.cut.of })">{{ t('playground.toolView.cut') }}</span>
    </template>
    <template #tools>
      <a class="code-tool" :href="page.url" target="_blank" rel="noopener noreferrer" :aria-label="t('playground.toolView.openPage')" :title="page.url">
        <AppIcon :icon="icons.ExternalLink" :size="18" />
      </a>
      <button
        type="button"
        class="code-tool"
        :aria-expanded="whole"
        :aria-label="whole ? t('playground.toolView.showLess') : t('playground.toolView.showAll')"
        :title="whole ? t('playground.toolView.showLess') : t('playground.toolView.showAll')"
        @click="whole = !whole"
      >
        <AppIcon :icon="icons.ChevronsUpDown" :size="18" />
      </button>
    </template>
    <div class="page app-scroll app-scroll-chain app-scroll-auto" :class="{ whole }">
      <Markdown class="type-body-medium" :source="page.body" :copy-label="t('playground.copyCode')" :copied-label="t('playground.codeCopied')" />
    </div>
  </CodeFrame>
  <RawOutput v-else-if="output" :text="output" :failed="failed" />
</template>

<style scoped>
.page { max-height: 320px; padding: var(--app-space-2) var(--app-space-3) var(--app-space-3); color: var(--md-sys-color-on-surface); }
.page.whole { max-height: none; }
a.code-tool { color: inherit; text-decoration: none; }
</style>
