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

/** A local calendar day as ``YYYY-MM-DD`` (not UTC: the day the reader is living in). */
export const localDay = (date: Date = new Date()): string =>
  `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}`;

/** ``day`` moved by ``n`` calendar days (local, so a daylight-saving change does not skip one). */
export const addDays = (day: string, n: number): string => {
  const [y, m, d] = day.split('-').map(Number);
  return localDay(new Date(y, m - 1, d + n));
};

/** A day for a heading: its weekday, month and date in the reader's language. */
export const longDay = (day: string): string => {
  const [y, m, d] = day.split('-').map(Number);
  const date = new Date(y, m - 1, d);
  return Number.isNaN(date.getTime()) ? day : date.toLocaleDateString(undefined, { weekday: 'short', month: 'short', day: 'numeric', year: y === new Date().getFullYear() ? undefined : 'numeric' });
};

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

/** A token count in a tile or an axis tick: `1284` → `1.3K`, `12_900_000` → `12.9M`; whole below a thousand. */
export const compact = (value: number | null | undefined): string => {
  if (value === null || value === undefined || !Number.isFinite(value)) return '—';
  const abs = Math.abs(value);
  const units: [number, string][] = [[1e12, 'T'], [1e9, 'B'], [1e6, 'M'], [1e3, 'K']];
  for (const [size, unit] of units) {
    if (abs >= size) {
      const scaled = value / size;
      return `${Math.abs(scaled) >= 100 ? Math.round(scaled) : scaled.toFixed(1).replace(/\.0$/, '')}${unit}`;
    }
  }
  return String(Math.round(value));
};

/** A share as a percentage: `0.4567` → `45.7%`; nothing to divide is a dash. */
export const percent = (value: number | null | undefined, digits = 1): string =>
  value === null || value === undefined || !Number.isFinite(value) ? '—' : `${(value * 100).toFixed(digits).replace(/\.0+$/, '')}%`;

/** A share of a whole, or null when there is no whole to share. */
export const ratio = (part: number, whole: number): number | null => (whole > 0 ? part / whole : null);

/** The change from ``before`` to ``now`` as a fraction, or null when there was nothing before. */
export const change = (now: number, before: number): number | null => (before > 0 ? (now - before) / before : null);

/** A duration for a person: `850` → `850 ms`, `12_400` → `12.4 s`, `95_000` → `1 min 35 s`. */
export const duration = (ms: number | null | undefined): string => {
  if (ms === null || ms === undefined || !Number.isFinite(ms)) return '—';
  if (ms < 1000) return `${Math.round(ms)} ms`;
  if (ms < 60_000) return `${(ms / 1000).toFixed(1)} s`;
  const minutes = Math.floor(ms / 60_000);
  const seconds = Math.round((ms % 60_000) / 1000);
  return seconds ? `${minutes} min ${seconds} s` : `${minutes} min`;
};

/**
 * How long ago (negative ``ms``) or from now (positive), in the reader's language: "3 minutes ago",
 * "in 2 days". The exact moment goes in a tooltip beside it.
 */
export const relative = (ms: number, locale: string): string => {
  const steps: [number, Intl.RelativeTimeFormatUnit][] = [[86_400_000, 'day'], [3_600_000, 'hour'], [60_000, 'minute'], [1000, 'second']];
  const format = new Intl.RelativeTimeFormat(locale, { numeric: 'auto' });
  for (const [size, unit] of steps) if (Math.abs(ms) >= size || unit === 'second') return format.format(Math.round(ms / size), unit);
  return '';
};

/** An hour bucket's axis label (`14:00`) or a day's (`10-21`), from the start the server reports. */
export const bucketLabel = (start: string, granularity: 'hour' | 'day'): string => {
  if (granularity === 'day') return dayLabel(start.slice(0, 10));
  const date = new Date(start);
  return Number.isNaN(date.getTime()) ? start : `${String(date.getHours()).padStart(2, '0')}:00`;
};
