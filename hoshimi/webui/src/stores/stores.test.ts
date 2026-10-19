import { createPinia, setActivePinia } from 'pinia';
import { beforeEach, describe, expect, it } from 'vitest';
import { nextTick } from 'vue';
import { useAuthStore } from '@/stores/auth';
import { DEFAULTS, usePreferencesStore } from '@/stores/preferences';

describe('preferences', () => {
  beforeEach(() => {
    localStorage.clear();
    setActivePinia(createPinia());
  });

  it('starts from the defaults', () => {
    expect({ ...usePreferencesStore().prefs }).toEqual(DEFAULTS);
  });

  it('reads what was saved, and drops a locale this build does not know', () => {
    localStorage.setItem('hoshimi:preferences', JSON.stringify({ theme: 'dark', locale: 'fr' }));
    const prefs = usePreferencesStore();
    expect(prefs.prefs.theme).toBe('dark');
    expect(prefs.prefs.locale).toBe('auto');
  });

  it('persists a change as it happens', async () => {
    const prefs = usePreferencesStore();
    prefs.prefs.theme = 'dark';
    await nextTick();
    expect(JSON.parse(localStorage.getItem('hoshimi:preferences') ?? '{}').theme).toBe('dark');
  });
});

describe('auth', () => {
  beforeEach(() => {
    localStorage.clear();
    setActivePinia(createPinia());
  });

  it('keeps the key in localStorage and stops asking for it', () => {
    const auth = useAuthStore();
    auth.needed = true;
    auth.setToken('secret');
    expect(localStorage.getItem('hoshimi:access-token')).toBe('secret');
    expect(auth.needed).toBe(false);
    auth.setToken(null);
    expect(localStorage.getItem('hoshimi:access-token')).toBeNull();
    expect(auth.token).toBeNull();
  });
});
