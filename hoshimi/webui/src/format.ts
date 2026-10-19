/**
 * Dashboard numbers: what was measured is printed, what was not is a dash.
 *
 * The server drops a rate it cannot stand behind (a decode window under 250 ms, or a rate past
 * 250 tok/s), so `null` travels all the way here. It is rendered as "—" rather than as a zero:
 * a missing measurement and a measured zero are different answers to different questions.
 */

/** Grouped digits, for a count the reader should recognise at a glance. */
export const grouped = (value: number | null | undefined): string =>
  value === null || value === undefined ? '—' : String(Math.round(value)).replace(/\B(?=(\d{3})+(?!\d))/g, ',');

/** A whole-token rate: the server only sends one it would defend. */
export const rate = (value: number | null | undefined): string => (value === null || value === undefined ? '—' : grouped(value));

/** A duration in milliseconds; an unknown first token is a dash, not a zero. */
export const millis = (value: number | null | undefined): string => (value === null || value === undefined ? '—' : grouped(value));

/** The portion of the day row's label worth a bar's width: `2026-10-11` → `10-11`. */
export const dayLabel = (day: string): string => (/^\d{4}-\d{2}-\d{2}$/.test(day) ? day.slice(5) : day);

/** The timestamp a sample carries, in the reader's own time zone. */
export const when = (iso: string): string => {
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? iso : date.toLocaleString();
};

/** A context window in tokens: `131072` → `128 K`, and nothing for a model that never said. */
export const contextWindow = (tokens: number | null | undefined): string => (tokens && tokens > 0 ? `${Math.round(tokens / 1024)} K` : '');

/** Probe verdict → badge tone. `unknown` is neutral on purpose: it is not a judgement. */
export const probeTone = (state: string): 'error' | 'primary' | 'neutral' | 'warning' => {
  if (state === 'available') return 'primary';
  if (state === 'region-blocked' || state === 'throttled') return 'warning';
  if (state === 'unavailable') return 'error';
  return 'neutral';
};

/** The states a filter offers, in the order a reader weighs them. */
export const PROBE_STATES = ['available', 'region-blocked', 'unavailable', 'throttled', 'unknown'] as const;

/** The facts a quota badge is decided from, all of them already read off a response. */
export interface QuotaFacts {
  percent: number | null;
  stale: boolean;
  rateLimited: number;
}

/**
 * A lane's reported allowance → badge. `null` means the lane said nothing worth a
 * badge: no readable percentage, and no rate limiting to report in its place.
 *
 * A percentage still earns a badge when stale, because a number the reader can
 * weigh as old beats a blank; `kind` is what says so.
 */
export const quotaBadge = (q: QuotaFacts | null): { kind: 'limited' | 'percent' | 'stale'; tone: 'error' | 'warning' | 'primary' } | null => {
  if (!q) return null;
  if (q.percent === null) return q.rateLimited > 0 ? { kind: 'limited', tone: 'warning' } : null;
  if (q.stale) return { kind: 'stale', tone: 'warning' };
  if (q.percent <= 10) return { kind: 'percent', tone: 'error' };
  if (q.percent <= 25) return { kind: 'percent', tone: 'warning' };
  return { kind: 'percent', tone: 'primary' };
};
