import { defineStore } from 'pinia';
import { reactive, watch } from 'vue';
import type { ThemeMode } from '@/theme/applyTheme';
import { DEFAULT_SOURCE_COLOR } from '@/theme/scheme';

export type Locale = 'en' | 'zh-CN' | 'ja';
/** A locale, or ``auto`` to follow the system's language. */
export type LocalePreference = Locale | 'auto';
const LOCALE_PREFERENCES: LocalePreference[] = ['auto', 'en', 'zh-CN', 'ja'];

export interface Preferences {
  theme: ThemeMode;
  sourceColor: string;
  contrast: number;
  locale: LocalePreference;
}

const STORAGE_KEY = 'hoshimi:preferences';

export const DEFAULTS: Preferences = {
  theme: 'system',
  sourceColor: DEFAULT_SOURCE_COLOR,
  contrast: 0,
  locale: 'auto',
};

function readLocal(): Partial<Preferences> {
  try {
    return JSON.parse(localStorage.getItem(STORAGE_KEY) ?? '{}');
  } catch {
    return {};
  }
}

function merge(base: Preferences, patch: Partial<Preferences>): Preferences {
  const merged = { ...base, ...patch };
  // A locale this build does not know (saved by a newer one, or edited by hand) follows the system.
  if (!LOCALE_PREFERENCES.includes(merged.locale)) merged.locale = 'auto';
  return merged;
}

/**
 * Interface preferences are client state: they persist to localStorage, so index.html can apply the
 * theme before the bundle loads. The server's own settings live in the settings page.
 */
export const usePreferencesStore = defineStore('preferences', () => {
  const prefs = reactive<Preferences>(merge(DEFAULTS, readLocal()));

  watch(
    prefs,
    (value) => {
      try {
        localStorage.setItem(STORAGE_KEY, JSON.stringify(value));
      } catch {
        /* private mode */
      }
    },
    { deep: true },
  );

  return { prefs };
});
