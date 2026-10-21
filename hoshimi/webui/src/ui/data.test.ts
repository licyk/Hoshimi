import { mount } from '@vue/test-utils';
import { describe, expect, it } from 'vitest';
import { nextTick } from 'vue';
import ChipInput from '@/ui/ChipInput.vue';
import DataTable from '@/ui/DataTable.vue';
import Meter from '@/ui/Meter.vue';
import StackedBarChart from '@/ui/StackedBarChart.vue';
import StatTile from '@/ui/StatTile.vue';

const rows = [
  { id: 'a', name: 'alpha', calls: 3 },
  { id: 'b', name: 'beta', calls: 10 },
  { id: 'c', name: 'gamma', calls: null },
];
const columns = [
  { key: 'name', label: 'Name', sortable: true },
  { key: 'calls', label: 'Calls', align: 'end' as const, sortable: true },
  { key: 'wide', label: 'Wide only', minWidth: 5000 },
];
const table = (props: Record<string, unknown> = {}) => mount(DataTable, { props: { columns, rows, rowKey: (r: unknown) => (r as { id: string }).id, label: 'Things', ...props } });
const cells = (w: ReturnType<typeof table>, i: number) => w.findAll('[role="row"].body').map((r) => r.findAll('[role="cell"]')[i].text());

describe('DataTable', () => {
  it('puts every row on the header’s columns and leaves out a column the width cannot hold', () => {
    const w = table();
    expect(w.findAll('[role="columnheader"]').map((h) => h.text())).toEqual(['Name', 'Calls']);
    expect(cells(w, 0)).toEqual(['alpha', 'beta', 'gamma']);
    expect(cells(w, 1)).toEqual(['3', '10', '—']);
  });

  it('sorts a number largest first on the first click and keeps missing values last', async () => {
    const w = table();
    await w.findAll('[role="columnheader"]')[1].find('button').trigger('click');
    expect(cells(w, 0)).toEqual(['beta', 'alpha', 'gamma']);
    expect(w.findAll('[role="columnheader"]')[1].attributes('aria-sort')).toBe('descending');
    await w.findAll('[role="columnheader"]')[1].find('button').trigger('click');
    expect(cells(w, 0)).toEqual(['alpha', 'beta', 'gamma']);
  });

  it('chooses a row by click or keyboard', async () => {
    const w = table({ clickable: true });
    await w.findAll('[role="row"].body')[1].trigger('keydown', { key: 'Enter' });
    expect(w.emitted('select')?.[0]).toEqual([rows[1]]);
  });

  it('renders only the rows in view once the list is long', () => {
    const many = Array.from({ length: 1000 }, (_, i) => ({ id: String(i), name: `n${i}`, calls: i }));
    const w = table({ rows: many });
    const shown = w.findAll('[role="row"].body').length;
    expect(shown).toBeGreaterThan(0);
    expect(shown).toBeLessThan(100);
  });
});

describe('StackedBarChart', () => {
  const series = [{ key: 'in', label: 'Input', slot: 1 }, { key: 'out', label: 'Output', slot: 2 }];
  const points = [{ key: 'd1', label: '10-20', values: [100, 50] }, { key: 'd2', label: '10-21', values: [0, 0] }];

  it('stacks one part per non-zero value and names every series in the legend', () => {
    const w = mount(StackedBarChart, { props: { series, points, label: 'Tokens' } });
    expect(w.findAll('.bar').map((b) => b.findAll('rect').length)).toEqual([2, 0]);
    expect(w.findAll('.legend .key').map((k) => k.text())).toEqual(['Input', 'Output']);
  });

  it('carries every value and the total in its table', () => {
    const w = mount(StackedBarChart, { props: { series, points, label: 'Tokens', totalLabel: 'Total' } });
    expect(w.findAll('tbody tr')[0].findAll('td').map((c) => c.text())).toEqual(['100', '50', '150']);
  });

  it('shows a column’s values and total on focus and arrow keys', async () => {
    const w = mount(StackedBarChart, { props: { series, points, label: 'Tokens', totalLabel: 'Total' } });
    await w.find('svg').trigger('keydown', { key: 'ArrowRight' });
    expect(w.find('.tip').text()).toContain('10-20');
    expect(w.find('.tip').text()).toContain('150');
    await w.find('svg').trigger('keydown', { key: 'Escape' });
    expect(w.find('.tip').exists()).toBe(false);
  });
});

describe('figures', () => {
  it('says a change against the previous period with its sign and whether it is good news', () => {
    const up = mount(StatTile, { props: { label: 'Tokens', value: 1200, delta: 0.25 } });
    expect(up.find('.value').text()).toBe('1,200');
    expect(up.find('.delta').text()).toBe('+25%');
    expect(up.find('.delta').classes()).toContain('good');
    const worse = mount(StatTile, { props: { label: 'Failures', value: 4, delta: 0.5, upIsGood: false } });
    expect(worse.find('.delta').classes()).toContain('bad');
    expect(mount(StatTile, { props: { label: 'Latency', value: null } }).find('.value').text()).toBe('—');
  });

  it('splits a meter by share and leaves out empty parts', () => {
    const w = mount(Meter, { props: { label: 'Input', segments: [{ label: 'a', value: 3, slot: 1 }, { label: 'b', value: 0, slot: 2 }, { label: 'c', value: 1, slot: 3 }] } });
    expect(w.findAll('.part').map((p) => (p.element as HTMLElement).style.flexGrow)).toEqual(['0.75', '0.25']);
  });

  it('adds typed and pasted values once each, and removes one', async () => {
    const w = mount(ChipInput, { props: { label: 'Hosts', values: ['a.example'] } });
    const input = w.findComponent({ name: 'TextField' });
    input.vm.$emit('update:modelValue', 'b.example, a.example c.example');
    await nextTick();
    input.vm.$emit('enter');
    expect(w.emitted('change')?.[0]).toEqual([['a.example', 'b.example', 'c.example']]);
    await w.find('.remove').trigger('click');
    expect(w.emitted('change')?.[1]).toEqual([[]]);
  });
});
