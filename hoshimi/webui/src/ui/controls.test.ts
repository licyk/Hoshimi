import { mount } from '@vue/test-utils';
import { describe, expect, it, vi } from 'vitest';
import { nextTick } from 'vue';
import DatePicker from '@/ui/DatePicker.vue';
import NumberField from '@/ui/NumberField.vue';
import SegmentedControl from '@/ui/SegmentedControl.vue';
import StackedBarChart from '@/ui/StackedBarChart.vue';

const options = [
  { value: 'all', label: 'All', count: 3 },
  { value: 'on', label: 'On', count: 2 },
  { value: 'off', label: 'Off', count: 1 },
];

describe('SegmentedControl', () => {
  it('is one radio group with a tab stop on the choice, and counts beside the labels', () => {
    const w = mount(SegmentedControl, { props: { options, modelValue: 'on', label: 'Filter' } });
    const radios = w.findAll('[role="radio"]');
    expect(w.find('[role="radiogroup"]').attributes('aria-label')).toBe('Filter');
    expect(radios.map((r) => r.text())).toEqual(['All3', 'On2', 'Off1']);
    expect(radios.map((r) => r.attributes('tabindex'))).toEqual(['-1', '0', '-1']);
    expect(radios[1].attributes('aria-checked')).toBe('true');
  });

  it('moves the choice with a click, the arrow keys, Home and End, wrapping at the ends', async () => {
    const w = mount(SegmentedControl, { props: { options, modelValue: 'all', label: 'Filter', 'onUpdate:modelValue': (v: string) => w.setProps({ modelValue: v }) } });
    const group = w.find('[role="radiogroup"]');
    await w.findAll('[role="radio"]')[2].trigger('click');
    expect(w.props('modelValue')).toBe('off');
    await group.trigger('keydown', { key: 'ArrowRight' });
    expect(w.props('modelValue')).toBe('all');
    await group.trigger('keydown', { key: 'ArrowLeft' });
    expect(w.props('modelValue')).toBe('off');
    await group.trigger('keydown', { key: 'Home' });
    expect(w.props('modelValue')).toBe('all');
    await group.trigger('keydown', { key: 'End' });
    expect(w.props('modelValue')).toBe('off');
  });
});

describe('DatePicker', () => {
  const open = async (props: Record<string, unknown> & { modelValue: string; max: string }) => {
    const w = mount(DatePicker, {
      props: { label: 'Pick a day', todayLabel: 'Today', previousLabel: 'Previous month', nextLabel: 'Next month', ...props },
      slots: { default: '<template #default="{ toggle }"><button class="trigger" @click="toggle">open</button></template>' },
      attachTo: document.body,
    });
    await w.find('.trigger').trigger('click');
    await nextTick();
    return w;
  };
  const day = (date: string) => document.querySelector<HTMLButtonElement>(`[data-date="${date}"]`)!;

  it('shows the chosen day’s month, refuses days past the last one, and shades the busy days', async () => {
    const w = await open({ modelValue: '2026-10-12', max: '2026-10-21', min: '2026-10-02', marks: { '2026-10-12': 1, '2026-10-05': 0.25 } });
    expect(document.querySelectorAll('[role="gridcell"]')).toHaveLength(31);
    expect(day('2026-10-12').getAttribute('aria-selected')).toBe('true');
    expect(day('2026-10-22').disabled).toBe(true);
    expect(day('2026-10-01').disabled).toBe(true);
    expect(day('2026-10-21').classList.contains('today')).toBe(true);
    expect(day('2026-10-05').classList.contains('marked')).toBe(true);
    expect(document.querySelector<HTMLButtonElement>('[aria-label="Next month"]')!.disabled).toBe(true);
    w.unmount();
  });

  it('picks a day by click or keys, and Today jumps to the last day', async () => {
    let value = '2026-10-12';
    const w = await open({ modelValue: value, max: '2026-10-21', 'onUpdate:modelValue': (v: string) => (value = v) });
    day('2026-10-14').click();
    expect(value).toBe('2026-10-14');
    await w.find('.trigger').trigger('click');
    await nextTick();
    const panel = document.querySelector<HTMLElement>('.calendar')!;
    panel.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowUp', bubbles: true }));
    await nextTick();
    panel.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
    expect(value).toBe('2026-10-05');
    await w.find('.trigger').trigger('click');
    await nextTick();
    document.querySelector<HTMLButtonElement>('.today-button')!.click();
    expect(value).toBe('2026-10-21');
    w.unmount();
  });
});

describe('StackedBarChart', () => {
  const series = [{ key: 'a', label: 'A', slot: 1 }];
  it('opens a column when it is selectable, by pointer or by keys', async () => {
    const points = [
      { key: '2026-10-20', label: '10-20', values: [3] },
      { key: '2026-10-21', label: '10-21', values: [5], muted: true },
    ];
    const w = mount(StackedBarChart, { props: { series, points, label: 'Tokens', selectable: true } });
    await w.findAll('.hit')[1].trigger('click');
    expect(w.emitted('select')).toEqual([['2026-10-21']]);
    await w.find('svg').trigger('keydown', { key: 'ArrowRight' });
    await w.find('svg').trigger('keydown', { key: 'Enter' });
    expect(w.emitted('select')![1]).toEqual(['2026-10-20']);
    expect(w.findAll('.column')[1].classes()).toContain('muted');
  });
});

describe('NumberField', () => {
  const field = (props: Record<string, unknown>) => mount(NumberField, { props: { increaseLabel: 'Increase', decreaseLabel: 'Decrease', ...props } });
  const shown = (w: ReturnType<typeof field>) => (w.find('md-outlined-text-field').element as unknown as { value: string }).value;
  const arrow = (w: ReturnType<typeof field>, label: string) => w.find(`button[aria-label="${label}"]`);

  it('steps at once and reports one change when the arrows pause', async () => {
    vi.useFakeTimers();
    try {
      const w = field({ modelValue: 3, min: 1 });
      for (let i = 0; i < 3; i++) await arrow(w, 'Increase').trigger('click');
      expect(shown(w)).toBe('6');
      expect(w.emitted('change')).toBeUndefined();
      vi.advanceTimersByTime(600);
      expect(w.emitted('change')).toEqual([['6']]);
    } finally {
      vi.useRealTimers();
    }
  });

  it('keeps to its bounds and its step grid, and disables an arrow at its bound', async () => {
    const w = field({ modelValue: 1000, min: 1, step: 1024 });
    await arrow(w, 'Increase').trigger('click');
    expect(shown(w)).toBe('1024');
    await arrow(w, 'Decrease').trigger('click');
    await arrow(w, 'Decrease').trigger('click');
    expect(shown(w)).toBe('1');
    expect(arrow(w, 'Decrease').attributes('disabled')).toBeDefined();
    const top = field({ modelValue: 65535, min: 0, max: 65535 });
    expect(arrow(top, 'Increase').attributes('disabled')).toBeDefined();
    expect(arrow(top, 'Decrease').attributes('disabled')).toBeUndefined();
  });

  it('keeps the arrows out of the tab order, and reports a waiting step on leaving the field', async () => {
    const w = field({ modelValue: 5 });
    expect(w.findAll('button').map((b) => b.attributes('tabindex'))).toEqual(['-1', '-1']);
    await arrow(w, 'Decrease').trigger('click');
    await w.find('md-outlined-text-field').trigger('focusout');
    expect(w.emitted('change')).toEqual([['4']]);
  });
});
