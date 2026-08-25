import { afterEach, describe, expect, it, vi } from 'vitest';
import { api } from './client';

/**
 * The dashboard's Fetch button sent an empty body.
 *
 * That made `days_back` arrive as null, so the backend fell back to the 72-hour
 * overlap the twice-daily cron sweep already covers — a window that, by the time
 * a human presses the button, contains nothing unseen. The sweep then reported
 * success and created almost nothing, which read as "fetching is broken".
 *
 * The depth is now part of the request, so these pin that it actually leaves the
 * browser. A default that lives only in the backend is one refactor away from
 * silently reverting to the emptied window.
 */
function stubFetch(): ReturnType<typeof vi.fn> {
  const spy = vi.fn(
    async () =>
      new Response(JSON.stringify({ run_ids: [1], skipped_sources: [], days_back: 30 }), {
        status: 202,
        headers: { 'Content-Type': 'application/json' },
      }),
  );
  vi.stubGlobal('fetch', spy);
  return spy;
}

const bodyOf = (spy: ReturnType<typeof vi.fn>): Record<string, unknown> =>
  JSON.parse((spy.mock.calls[0][1] as RequestInit).body as string);

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('fetchNow carries the window it wants searched', () => {
  it('sends the requested depth rather than leaving it to a backend default', async () => {
    const spy = stubFetch();
    await api.fetchNow({ daysBack: 30 });
    expect(bodyOf(spy).days_back).toBe(30);
  });

  it('sends a single source alongside the depth', async () => {
    const spy = stubFetch();
    await api.fetchNow({ sources: ['ted'], daysBack: 7 });
    const body = bodyOf(spy);
    expect(body.sources).toEqual(['ted']);
    expect(body.days_back).toBe(7);
  });

  it('omits sources entirely when sweeping everything, never sends an empty list', async () => {
    // An empty array would be a different request: `sources: []` is falsy to the
    // route's `if payload.sources` check today, but relying on that is fragile.
    const spy = stubFetch();
    await api.fetchNow({ daysBack: 3 });
    expect('sources' in bodyOf(spy)).toBe(false);
  });

  it('still posts a depth when the caller passes none, so the emptied window cannot return', async () => {
    const spy = stubFetch();
    await api.fetchNow();
    expect(typeof bodyOf(spy).days_back).toBe('number');
    expect(bodyOf(spy).days_back).toBeGreaterThan(3);
  });

  it('posts to the fetch endpoint with the right method', async () => {
    const spy = stubFetch();
    await api.fetchNow({ daysBack: 30 });
    expect(spy.mock.calls[0][0]).toContain('/api/fetch');
    expect((spy.mock.calls[0][1] as RequestInit).method).toBe('POST');
  });
});
