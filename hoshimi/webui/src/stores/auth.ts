import { defineStore } from 'pinia';
import { ref } from 'vue';

const KEY = 'hoshimi:access-token';

function readToken(): string | null {
  try {
    return localStorage.getItem(KEY);
  } catch {
    return null;
  }
}

/** The tool access key, used by this browser for management and Playground requests. */
export const useAuthStore = defineStore('auth', () => {
  const token = ref<string | null>(readToken());
  /** Set when the server answers 401, so the dialog can ask for the key. */
  const needed = ref(false);

  function setToken(value: string | null) {
    token.value = value;
    try {
      if (value) localStorage.setItem(KEY, value);
      else localStorage.removeItem(KEY);
    } catch {
      /* private mode: keep it in memory */
    }
    needed.value = false;
  }
  return { token, needed, setToken };
});
