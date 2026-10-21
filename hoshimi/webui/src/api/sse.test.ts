import { describe, expect, it } from 'vitest';
import { parseFrame } from '@/api/sse';

describe('event stream frames', () => {
  it('joins a frame’s data lines into one JSON event', () => {
    expect(parseFrame('event: x\ndata: {"type":"started",\ndata: "n":1}')).toEqual({ type: 'started', n: 1 });
  });
  it('skips comments and pings', () => {
    expect(parseFrame(': keep-alive')).toBeUndefined();
    expect(parseFrame('event: ping\ndata: {"type":"ping"}')).toBeUndefined();
  });
});
