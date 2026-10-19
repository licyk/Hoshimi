<script setup lang="ts">
import { computed, onBeforeUnmount, watch } from 'vue';
import { RouterView } from 'vue-router';
import AuthDialog from '@/components/AuthDialog.vue';
import { useI18n } from '@/i18n';
import { addressOf } from '@/router';
import { usePreferencesStore } from '@/stores/preferences';
import { applyTheme, watchSystemTheme } from '@/theme/applyTheme';
import { AppShell, IconButton, TRANSITIONS, icons, type NavItem } from '@/ui';

const { t, locale } = useI18n();
const prefs = usePreferencesStore();

const themeOptions = () => ({ mode: prefs.prefs.theme, sourceColor: prefs.prefs.sourceColor, contrast: prefs.prefs.contrast });
watch(() => [prefs.prefs.theme, prefs.prefs.sourceColor, prefs.prefs.contrast], () => applyTheme(themeOptions()), { immediate: true });
watch(locale, (l) => (document.documentElement.lang = l), { immediate: true });
const stopTheme = watchSystemTheme(themeOptions);
onBeforeUnmount(stopTheme);

const nav = computed<NavItem[]>(() => [
  { to: addressOf('overview'), label: t('nav.overview'), icon: icons.LayoutGrid },
  { to: addressOf('models'), label: t('nav.models'), icon: icons.Layers },
  { to: addressOf('channels'), label: t('nav.channels'), icon: icons.Fingerprint },
  { to: addressOf('usage'), label: t('nav.usage'), icon: icons.BarChart3 },
  { to: addressOf('playground'), label: t('nav.playground'), icon: icons.Sparkles },
]);
// Settings sits at the foot of the rail, in the bottom corner whatever the window's height (last in the bottom bar).
const footer = computed<NavItem[]>(() => [{ to: addressOf('settings'), label: t('nav.settings'), icon: icons.Settings }]);

const cycleTheme = () => (prefs.prefs.theme = prefs.prefs.theme === 'light' ? 'dark' : prefs.prefs.theme === 'dark' ? 'system' : 'light');
const themeIcon = computed(() => ({ light: icons.Sun, dark: icons.Moon, system: icons.SunMoon })[prefs.prefs.theme]);
</script>

<template>
  <AppShell :items="nav" :footer="footer" :title="t('app.title')">
    <template #rail-top>
      <IconButton :icon="icons.Constellation" :label="t('app.title')" tonal />
    </template>
    <template #actions>
      <IconButton :icon="themeIcon" :label="`${t('settings.theme')}: ${t(`settings.themes.${prefs.prefs.theme}`)}`" @click="cycleTheme" />
    </template>
    <RouterView v-slot="{ Component, route }">
      <Transition :name="TRANSITIONS.fadeThrough" mode="out-in">
        <KeepAlive>
          <component :is="Component" :key="route.name" />
        </KeepAlive>
      </Transition>
    </RouterView>
  </AppShell>
  <AuthDialog />
</template>
