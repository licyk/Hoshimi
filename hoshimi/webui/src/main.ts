import { QueryClient, VueQueryPlugin } from '@tanstack/vue-query';
import { createPinia } from 'pinia';
import { createApp } from 'vue';
import App from '@/App.vue';
import { ApiError } from '@/api/client';
import { router } from '@/router';
import '@/theme/tokens.css';
import { applyScrollbars } from '@/theme/scrollbars';

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      refetchOnWindowFocus: false,
      // Client errors (bad input, not found, needs a token) are not worth retrying.
      retry: (count, error) => !(error instanceof ApiError && error.status >= 400 && error.status < 500) && count < 2,
    },
  },
});

applyScrollbars();
createApp(App).use(createPinia()).use(router).use(VueQueryPlugin, { queryClient }).mount('#app');
