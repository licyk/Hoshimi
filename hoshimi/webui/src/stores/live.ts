import type { QueryClient } from '@tanstack/vue-query';
import { defineStore } from 'pinia';
import { computed, ref, watch } from 'vue';
import { ApiError } from '@/api/client';
import { keys } from '@/api/queries/keys';
import { readEventStream } from '@/api/sse';
import type { CallRecord, LiveCall, LiveEvent, UsageTotals } from '@/api/types';
import { useAuthStore } from '@/stores/auth';

export type LiveState = 'connecting' | 'live' | 'offline';

const RECENT = 20;
const BACKOFF_MS = [1000, 2000, 4000, 8000, 15000, 30000];
/** Finished calls within this window cause one refetch of the reports, not one each. */
const SETTLE_MS = 300;
/** How often a running call's clock moves on screen. */
const TICK_MS = 250;

/**
 * The calls running through the gateway right now, today's totals and the last few calls, kept
 * current by the server's live stream (``/api/v1/stats/live``). REST stays the truth: a call that
 * finishes patches what is here and invalidates the usage and channel reports, which refetch; a
 * reconnect starts from a fresh snapshot, so a missed event never leaves the screen stale.
 *
 * One connection serves the whole interface (the activity button, the Usage page, the Overview
 * card). It retries with backoff when the server goes away, and waits for a new access key when
 * the server refuses the one it has.
 */
export const useLiveStore = defineStore('live', () => {
  const state = ref<LiveState>('offline');
  const active = ref<LiveCall[]>([]);
  const today = ref<UsageTotals | null>(null);
  const recent = ref<CallRecord[]>([]);
  /** The server's clock minus this browser's, so elapsed times agree with the server's start times. */
  const offset = ref(0);
  const now = ref(Date.now());

  let controller: AbortController | null = null;
  let client: QueryClient | null = null;
  let attempt = 0;
  let retry: ReturnType<typeof setTimeout> | undefined;
  let settle: ReturnType<typeof setTimeout> | undefined;
  let ticker: ReturnType<typeof setInterval> | undefined;
  let stopAuth: (() => void) | null = null;

  // The clock only runs while something is running.
  watch(
    () => active.value.length > 0,
    (running) => {
      clearInterval(ticker);
      ticker = undefined;
      if (running) {
        now.value = Date.now();
        ticker = setInterval(() => (now.value = Date.now()), TICK_MS);
      }
    },
  );

  /** A running call's age in ms on the server's clock, moving with ``now``. */
  const elapsed = (call: LiveCall) => Math.max(0, now.value + offset.value - call.started_at * 1000);

  function settleReports() {
    clearTimeout(settle);
    settle = setTimeout(() => {
      client?.invalidateQueries({ queryKey: keys.stats });
      client?.invalidateQueries({ queryKey: keys.channels });
    }, SETTLE_MS);
  }

  function upsert(call: LiveCall) {
    const i = active.value.findIndex((c) => c.id === call.id);
    if (i === -1) active.value = [...active.value, call];
    else active.value = active.value.map((c, j) => (j === i ? call : c));
  }

  function apply(event: LiveEvent) {
    switch (event.type) {
      case 'snapshot':
        offset.value = event.server_time * 1000 - Date.now();
        active.value = event.active;
        today.value = event.today;
        recent.value = event.recent.slice(0, RECENT);
        break;
      case 'started':
      case 'progress':
        upsert(event.call);
        break;
      case 'finished':
        active.value = active.value.filter((c) => c.id !== event.call_id);
        recent.value = [event.record, ...recent.value.filter((r) => r.id !== event.record.id)].slice(0, RECENT);
        today.value = event.today;
        settleReports();
        break;
    }
  }

  async function run(signal: AbortSignal) {
    state.value = 'connecting';
    try {
      await readEventStream('/api/v1/stats/live', {
        signal,
        onOpen: () => {
          state.value = 'live';
          attempt = 0;
        },
        onEvent: (event) => apply(event as LiveEvent),
      });
    } catch (error) {
      if (signal.aborted) return;
      // A refused key: nothing to retry until the key changes (the auth store's watcher reconnects).
      if (error instanceof ApiError && (error.status === 401 || error.status === 403)) {
        state.value = 'offline';
        return;
      }
    }
    if (signal.aborted) return;
    // The server ended the stream or went away: what was running is unknown until the next snapshot.
    state.value = 'connecting';
    retry = setTimeout(() => open(), BACKOFF_MS[Math.min(attempt++, BACKOFF_MS.length - 1)]);
  }

  function open() {
    controller?.abort();
    clearTimeout(retry);
    controller = new AbortController();
    void run(controller.signal);
  }

  function connect(qc: QueryClient) {
    client = qc;
    if (!stopAuth) stopAuth = watch(() => useAuthStore().token, () => open());
    attempt = 0;
    open();
  }

  function disconnect() {
    controller?.abort();
    controller = null;
    clearTimeout(retry);
    clearTimeout(settle);
    clearInterval(ticker);
    stopAuth?.();
    stopAuth = null;
    state.value = 'offline';
  }

  const totalTokens = computed(() => (today.value ? today.value.input + today.value.output : 0));
  return { state, active, today, recent, now, elapsed, totalTokens, connect, disconnect, apply };
});
