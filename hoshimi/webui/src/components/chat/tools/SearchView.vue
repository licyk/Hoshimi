<script setup lang="ts">
import { computed } from 'vue';
import CodeFrame from '@/components/chat/tools/CodeFrame.vue';
import RawOutput from '@/components/chat/tools/RawOutput.vue';
import { hostOf, parseSearch } from '@/components/chat/tools/parse';
import { useI18n } from '@/i18n';
import { icons } from '@/ui';

/** A ``web_search``'s results: each page's title (a link that opens beside the app), its site and what it says. */
const props = defineProps<{ output: string; failed: boolean }>();
const { t } = useI18n();
const found = computed(() => (props.failed ? null : parseSearch(props.output)));
</script>

<template>
  <CodeFrame
    v-if="found"
    :icon="icons.Search"
    :title="`“${found.query}”`"
    :copy="found.results.map((r) => `${r.title}\n${r.url}`).join('\n\n')"
  >
    <template #tags><span class="code-tag">{{ found.results.length ? t('playground.toolView.results', { n: found.results.length }) : t('playground.toolView.noResults') }}</span></template>
    <ol v-if="found.results.length" class="results app-scroll app-scroll-chain app-scroll-auto">
      <li v-for="(r, i) in found.results" :key="i">
        <a class="title type-body-medium" :href="r.url" target="_blank" rel="noopener noreferrer">{{ r.title || r.url }}</a>
        <span class="site type-body-small">{{ hostOf(r.url) }}</span>
        <span v-if="r.snippet" class="snippet type-body-small">{{ r.snippet }}</span>
      </li>
    </ol>
  </CodeFrame>
  <RawOutput v-else-if="output" :text="output" :failed="failed" />
</template>

<style scoped>
.results { display: flex; flex-direction: column; max-height: 420px; margin: 0; padding: 0; list-style: none; }
.results li { display: flex; flex-direction: column; gap: 2px; min-width: 0; padding: var(--app-space-2) var(--app-space-3); }
.results li + li { border-top: 1px solid var(--md-sys-color-outline-variant); }
.title { color: var(--md-sys-color-primary); font-weight: 500; text-decoration: none; overflow-wrap: anywhere; }
.title:hover { text-decoration: underline; text-underline-offset: 2px; }
.site { color: var(--md-sys-color-on-surface-variant); overflow: hidden; white-space: nowrap; text-overflow: ellipsis; }
.snippet {
  display: -webkit-box; -webkit-box-orient: vertical; -webkit-line-clamp: 3; line-clamp: 3; overflow: hidden;
  color: var(--md-sys-color-on-surface); overflow-wrap: anywhere;
}
</style>
