import { reactive } from 'vue';
import { createRouter, createWebHashHistory, type RouteRecordRaw } from 'vue-router';

const routes: RouteRecordRaw[] = [
  { path: '/', redirect: '/overview' },
  { path: '/overview', name: 'overview', component: () => import('@/views/OverviewView.vue') },
  { path: '/models', name: 'models', component: () => import('@/views/ModelsView.vue') },
  { path: '/channels', name: 'channels', component: () => import('@/views/ChannelsView.vue') },
  { path: '/usage', name: 'usage', component: () => import('@/views/UsageView.vue') },
  { path: '/playground', name: 'playground', component: () => import('@/views/PlaygroundView.vue') },
  { path: '/settings', name: 'settings', component: () => import('@/views/SettingsView.vue') },
  { path: '/:pathMatch(.*)*', redirect: '/overview' },
];

// Hash history: the UI works at any deployment sub-path with no server-side rewrites.
export const router = createRouter({ history: createWebHashHistory(), routes });

// The address each page was last at, so the navigation goes back to where it left rather than to
// the page's start. Memory only: a reload starts every page afresh.
const lastAddress = reactive<Record<string, string>>({});
router.afterEach((to) => {
  if (typeof to.name === 'string') lastAddress[to.name] = to.fullPath;
});

/** Where the navigation takes the page ``name``: its last address in this session, else its start. */
export const addressOf = (name: string): string => lastAddress[name] ?? `/${name}`;
