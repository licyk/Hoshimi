import { createPinia, setActivePinia } from 'pinia';
import { beforeEach, describe, expect, it } from 'vitest';
import { handleToolAuthentication } from '@/api/client';
import { useAuthStore } from '@/stores/auth';

describe('tool authentication failures', () => {
  beforeEach(() => {
    localStorage.clear();
    setActivePinia(createPinia());
    useAuthStore().setToken('current');
  });

  it('opens automatic login only for a current tool credential refusal', async () => {
    const denied = () => new Response('{"code":"auth_required"}', { status: 401 });
    await handleToolAuthentication(denied(), 'Bearer previous');
    expect(useAuthStore().needed).toBe(false);
    await handleToolAuthentication(new Response('{"code":"model_api_key_required"}', { status: 401 }), 'Bearer current');
    expect(useAuthStore().needed).toBe(false);
    await handleToolAuthentication(denied(), 'Bearer current');
    expect(useAuthStore().needed).toBe(true);
  });

  it('ignores an old credential rejection whose body arrives after key rotation', async () => {
    let release!: (body: unknown) => void;
    const response = { status: 401, clone: () => ({ json: () => new Promise((resolve) => { release = resolve; }) }) } as Response;
    const check = handleToolAuthentication(response, 'Bearer current');
    useAuthStore().setToken('new');
    release({ code: 'auth_required' });
    await check;
    expect(useAuthStore().needed).toBe(false);
  });
});
