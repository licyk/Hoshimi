<script setup lang="ts">
import { useQueryClient } from '@tanstack/vue-query';
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue';
import { RouterView, useRoute, useRouter } from 'vue-router';
import { useChannels } from '@/api/queries/channels';
import AuthDialog from '@/components/AuthDialog.vue';
import LiveSheet from '@/components/LiveSheet.vue';
import { useI18n } from '@/i18n';
import { addressOf } from '@/router';
import { useLiveStore } from '@/stores/live';
import { usePreferencesStore } from '@/stores/preferences';
import { applyTheme, watchSystemTheme } from '@/theme/applyTheme';
import { AppShell, IconButton, TRANSITIONS, icons, installViewTransitions, type NavItem } from '@/ui';

const { t, locale } = useI18n();
const prefs = usePreferencesStore();
const live = useLiveStore();
const qc = useQueryClient();
const route = useRoute();
const router = useRouter();
const liveOpen = ref(false);
// Channels that need a hand (a login, an expired token, an exhausted allowance) mark the rail.
const channels = useChannels();
const attention = computed(() => channels.data.value?.channels.filter((c) => c.status === 'attention').length ?? 0);

const themeOptions = () => ({ mode: prefs.prefs.theme, sourceColor: prefs.prefs.sourceColor, contrast: prefs.prefs.contrast });
watch(() => [prefs.prefs.theme, prefs.prefs.sourceColor, prefs.prefs.contrast], () => applyTheme(themeOptions()), { immediate: true });
watch(() => prefs.prefs.motion, (m) => (m === 'reduced' ? (document.documentElement.dataset.motion = 'reduced') : delete document.documentElement.dataset.motion), { immediate: true });
watch(locale, (l) => (document.documentElement.lang = l), { immediate: true });
const stopTheme = watchSystemTheme(themeOptions);

/** One kept-alive instance per destination: a channel's detail is a state of the Channels page. */
const viewKey = (name: unknown) => String(name);

// Pages are kept alive, so each keeps its state while another is shown; the shell's scroller is
// shared, so each page's offset in it is kept here and put back before the page is seen again
// (inside the view transition's update, so there is no jump). Memory only: a refresh resets all.
// Registered before the view transitions so the offset is in place when their update finishes.
const shell = ref<InstanceType<typeof AppShell> | null>(null);
const offsets = new Map<string, number>();
router.beforeEach((to, from) => {
  if (from.name && to.name !== from.name) offsets.set(viewKey(from.name), shell.value?.content?.scrollTop ?? 0);
  return true;
});
function restoreOffset() {
  const content = shell.value?.content;
  if (content) content.scrollTop = offsets.get(viewKey(route.name)) ?? 0;
}
router.afterEach((to, from) => {
  if (viewTransitions.value && to.name !== from.name) nextTick(restoreOffset);
});
const viewTransitions = installViewTransitions(router);

// One live connection for the whole interface: the activity button, the Usage page and the
// Overview card read the same store, and a finished call refreshes the reports they show.
onMounted(() => live.connect(qc));
onBeforeUnmount(() => {
  live.disconnect();
  stopTheme();
});

const nav = computed<NavItem[]>(() => [
  { to: addressOf('overview'), label: t('nav.overview'), icon: icons.LayoutGrid },
  { to: addressOf('models'), label: t('nav.models'), icon: icons.Layers },
  { to: addressOf('channels'), label: t('nav.channels'), icon: icons.Fingerprint, badge: attention.value || undefined },
  { to: addressOf('usage'), label: t('nav.usage'), icon: icons.BarChart3 },
  { to: addressOf('playground'), label: t('nav.playground'), icon: icons.Sparkles },
]);
// Settings sits at the foot of the rail, in the bottom corner whatever the window's height (last in the bottom bar).
const footer = computed<NavItem[]>(() => [{ to: addressOf('settings'), label: t('nav.settings'), icon: icons.Settings }]);

const NEXT_THEME = { light: 'dark', dark: 'system', system: 'light' } as const;
const cycleTheme = () => (prefs.prefs.theme = NEXT_THEME[prefs.prefs.theme]);
const themeIcon = computed(() => ({ light: icons.Sun, dark: icons.Moon, system: icons.SunMoon })[prefs.prefs.theme]);
const running = computed(() => live.active.length);
const liveLabel = computed(() => (running.value ? t('live.chip', { n: running.value }) : t('live.idle')));
</script>

<template>
  <AppShell ref="shell" :items="nav" :footer="footer" :title="t('app.title')" :fill="route.meta.fill === true">
    <template #rail-top>
      <IconButton :icon="icons.Constellation" :label="t('app.title')" tonal />
    </template>
    <template #actions>
      <IconButton :icon="running ? icons.Loader2 : icons.Activity" :spin="running > 0" :label="liveLabel" :badge="running || null" @click="liveOpen = true" />
      <IconButton :icon="themeIcon" :label="`${t('settings.theme')}: ${t(`settings.themes.${prefs.prefs.theme}`)}`" @click="cycleTheme" />
    </template>
    <RouterView v-slot="{ Component, route: r }">
      <!-- With view transitions the browser animates the change; the view must render at once. -->
      <KeepAlive v-if="viewTransitions" :max="8">
        <component :is="Component" :key="viewKey(r.name)" />
      </KeepAlive>
      <Transition v-else :name="TRANSITIONS.fadeThrough" mode="out-in" @enter="restoreOffset">
        <KeepAlive :max="8">
          <component :is="Component" :key="viewKey(r.name)" />
        </KeepAlive>
      </Transition>
    </RouterView>
  </AppShell>
  <LiveSheet v-model:open="liveOpen" />
  <AuthDialog />
</template>
