import { describe, expect, it } from 'vitest';
import { PROBE_STATES, contextWindow, dayLabel, grouped, millis, probeTone, quotaBadge, rate, when } from '@/format';

describe('grouped', () => {
  it('groups digits the way a count is read', () => {
    expect(grouped(0)).toBe('0');
    expect(grouped(999)).toBe('999');
    expect(grouped(1000)).toBe('1,000');
    expect(grouped(1234567)).toBe('1,234,567');
    expect(grouped(12.6)).toBe('13');
  });
  it('shows a dash for a number the server refuses to claim', () => {
    expect(grouped(null)).toBe('—');
    expect(grouped(undefined)).toBe('—');
    expect(rate(null)).toBe('—');
    expect(millis(null)).toBe('—');
    expect(rate(84)).toBe('84');
    expect(millis(1875)).toBe('1,875');
  });
});

describe('dayLabel', () => {
  it('keeps the month and the day of a full date', () => {
    expect(dayLabel('2026-10-11')).toBe('10-11');
    expect(dayLabel('2026-01-05')).toBe('01-05');
  });
  it('leaves a label it does not recognise alone', () => {
    expect(dayLabel('today')).toBe('today');
  });
});

describe('contextWindow', () => {
  it('prints the window in K, and nothing when a model never declared one', () => {
    expect(contextWindow(131072)).toBe('128 K');
    expect(contextWindow(1048576)).toBe('1024 K');
    expect(contextWindow(0)).toBe('');
    expect(contextWindow(null)).toBe('');
  });
});

describe('probeTone', () => {
  it('gives each verdict the weight it earned', () => {
    expect(probeTone('available')).toBe('primary');
    expect(probeTone('unavailable')).toBe('error');
    expect(probeTone('region-blocked')).toBe('warning');
    expect(probeTone('throttled')).toBe('warning');
    // Unknown is not a judgement against the model.
    expect(probeTone('unknown')).toBe('neutral');
    expect(probeTone('anything else')).toBe('neutral');
  });
  it('offers every verdict a filter can ask for', () => {
    expect(PROBE_STATES).toContain('available');
    expect(PROBE_STATES).toContain('region-blocked');
    expect(PROBE_STATES).toHaveLength(5);
  });
});

describe('when', () => {
  it('falls back to the raw timestamp when there is no date in it', () => {
    expect(when('not a date')).toBe('not a date');
  });
});

describe('quotaBadge', () => {
  it('gives a lane no badge when it said nothing worth one', () => {
    // No readable percentage and no rate limiting: the "no data" chip a reader
    // would have to learn to ignore.
    expect(quotaBadge(null)).toBeNull();
    expect(quotaBadge({ percent: null, stale: false, rateLimited: 0 })).toBeNull();
  });
  it('weights a percentage by how little of it is left', () => {
    expect(quotaBadge({ percent: 80, stale: false, rateLimited: 0 })).toEqual({ kind: 'percent', tone: 'primary' });
    expect(quotaBadge({ percent: 26, stale: false, rateLimited: 0 })).toEqual({ kind: 'percent', tone: 'primary' });
    expect(quotaBadge({ percent: 25, stale: false, rateLimited: 0 })).toEqual({ kind: 'percent', tone: 'warning' });
    expect(quotaBadge({ percent: 11, stale: false, rateLimited: 0 })).toEqual({ kind: 'percent', tone: 'warning' });
    expect(quotaBadge({ percent: 10, stale: false, rateLimited: 0 })).toEqual({ kind: 'percent', tone: 'error' });
    expect(quotaBadge({ percent: 0, stale: false, rateLimited: 0 })).toEqual({ kind: 'percent', tone: 'error' });
  });
  it('still shows an old percentage, but says it is old', () => {
    // A number the reader can weigh as stale beats a blank one — but it must
    // not read as a current figure.
    expect(quotaBadge({ percent: 80, stale: true, rateLimited: 0 })).toEqual({ kind: 'stale', tone: 'warning' });
    expect(quotaBadge({ percent: 5, stale: true, rateLimited: 0 })).toEqual({ kind: 'stale', tone: 'warning' });
  });
  it('reports the rate limiting in place of a percentage it could not read', () => {
    expect(quotaBadge({ percent: null, stale: false, rateLimited: 3 })).toEqual({ kind: 'limited', tone: 'warning' });
  });
});
