import { createPinia, setActivePinia } from 'pinia';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { LiveCall } from '@/api/types';
import { useLiveStore } from '@/stores/live';

const zero = { input: 0, output: 0, reasoning: 0, cache_read: 0, cache_write: 0, calls: 0, failed: 0, no_usage: 0 };
const call = (id: string, phase: LiveCall['phase'] = 'waiting'): LiveCall => ({ id, model: 'm', name: 'M', provider: 'p', account: 'a', origin: 'chat', effort: '', stream: true, started_at: 1000, phase, ttft_ms: null, elapsed_ms: 0, output_chars: 0, reasoning_chars: 0, est_output_tokens: 0, tps: null, attempts: 1 });
const record = (id: string) => ({ seq: 1, id, at: '2026-10-21T10:00:00+00:00', model: 'm', name: 'M', provider: 'p', account: 'a', origin: 'chat', effort: '', stream: true, ok: true, input: 10, output: 5, reasoning: 0, cache_read: 0, cache_write: 0, ttft_ms: 100, decode_ms: 300, tps: 16, elapsed_ms: 400, attempts: 1, finish_reason: 'stop', error_code: '', error_message: '', no_usage: false });

describe('the live store', () => {
  beforeEach(() => setActivePinia(createPinia()));

  it('starts from the snapshot, follows each call and moves it to the finished list', () => {
    const live = useLiveStore();
    live.apply({ type: 'snapshot', server_time: 2000, active: [call('a')], today: zero, recent: [] });
    live.apply({ type: 'started', call: call('b') });
    live.apply({ type: 'progress', call: call('a', 'answering') });
    expect(live.active.map((c) => [c.id, c.phase])).toEqual([['a', 'answering'], ['b', 'waiting']]);
    live.apply({ type: 'finished', call_id: 'a', record: record('a'), today: { ...zero, input: 10, output: 5, calls: 1 } });
    expect(live.active.map((c) => c.id)).toEqual(['b']);
    expect(live.recent.map((r) => r.id)).toEqual(['a']);
    expect(live.totalTokens).toBe(15);
  });

  it('measures a running call on the server clock', () => {
    vi.useFakeTimers();
    vi.setSystemTime(500_000);
    const live = useLiveStore();
    // The server is 1.5 s ahead of this browser; the call started 2 s ago by its clock.
    live.apply({ type: 'snapshot', server_time: 501.5, active: [], today: zero, recent: [] });
    expect(live.elapsed({ ...call('x'), started_at: 499.5 })).toBe(2000);
    vi.useRealTimers();
  });

  it('keeps only the last twenty finished calls', () => {
    const live = useLiveStore();
    for (let i = 0; i < 25; i++) live.apply({ type: 'finished', call_id: `c${i}`, record: record(`c${i}`), today: zero });
    expect(live.recent).toHaveLength(20);
    expect(live.recent[0].id).toBe('c24');
  });
});
