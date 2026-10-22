import { mount } from '@vue/test-utils';
import { createPinia } from 'pinia';
import { describe, expect, it, vi } from 'vitest';
import { ref } from 'vue';
import type { UpdateInfo } from '@/api/types';
import OverviewView from '@/views/OverviewView.vue';

const update = ref<UpdateInfo | undefined>(undefined);

vi.mock('vue-router', () => ({ RouterLink: { template: '<a><slot :navigate="() => {}" /></a>' } }));
vi.mock('@/api/queries/app', () => ({
  useMeta: () => ({ data: ref({ auth_required: false, default_model: null, version: '0.3.4' }) }),
  useVersion: () => ({ data: ref({ version: '0.3.4' }) }),
  useUpdate: () => ({ data: update }),
}));
vi.mock('@/api/queries/channels', () => ({ useChannels: () => ({ data: ref(undefined), isPending: ref(false) }) }));
vi.mock('@/api/queries/usage', () => ({ useUsageReport: () => ({ data: ref(undefined) }) }));
vi.mock('@/api/catalog', () => ({ useCatalog: () => ({ data: ref(undefined) }) }));
vi.mock('@/components/CatalogStatus.vue', () => ({ default: { template: '<div />' } }));
vi.mock('@/ui', () => {
  const slot = { template: '<div><slot /></div>' };
  return {
    icons: {}, AppButton: slot, EmptyState: slot, IconButton: slot, LiveDot: slot, Skeleton: slot, Sparkline: slot,
    Badge: { props: ['value', 'tone'], template: '<span class="badge" :data-tone="tone">{{ value }}</span>' },
    DataList: { props: ['rows'], template: '<dl><template v-for="r in rows" :key="r.label"><dt>{{ r.label }}</dt><dd>{{ r.value }}</dd></template><slot /></dl>' },
    PageSection: { props: ['title'], template: '<section><h2>{{ title }}</h2><slot /></section>' },
    useSnackbar: () => ({ show: vi.fn(), error: vi.fn() }),
  };
});

/** The server section's rows, as label → value text. */
function about(): Record<string, string> {
  const w = mount(OverviewView, { global: { plugins: [createPinia()] } });
  const section = w.findAll('section').find((s) => s.find('h2').text() === 'Server')!;
  const labels = section.findAll('dt').map((d) => d.text());
  const values = section.findAll('dd').map((d) => (d.element.children.length ? [...d.element.children].map((c) => c.textContent?.trim()).join(' ') : d.text()));
  return Object.fromEntries(labels.map((l, i) => [l, values[i]]));
}

describe('overview: server', () => {
  it('names the newer release PyPI lists', () => {
    update.value = { status: 'available', current: '0.3.4', latest: '0.3.5', checked_at: 1 };
    expect(about()).toEqual({ Version: '0.3.4', 'Latest version': '0.3.5 Update available', 'Default model': 'Not set' });
  });

  it('says the running release is the latest', () => {
    update.value = { status: 'current', current: '0.3.4', latest: '0.3.4', checked_at: 1 };
    expect(about()['Latest version']).toBe('0.3.4 Up to date');
  });

  it('says when the check is still out or could not be made', () => {
    update.value = { status: 'checking', current: '0.3.4', latest: null, checked_at: null };
    expect(about()['Latest version']).toBe('Checking…');
    update.value = { status: 'failed', current: '0.3.4', latest: null, checked_at: 2 };
    expect(about()['Latest version']).toBe('Could not check');
  });

  it('leaves the row out where the check is switched off', () => {
    update.value = { status: 'disabled', current: '0.3.4', latest: null, checked_at: null };
    expect(about()).not.toHaveProperty('Latest version');
  });
});
