import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

/**
 * Opening a source and seeing what it brought.
 *
 * Every source surface reported a stored count and none of them could open it.
 * The count was the one fact on the card a reader was most likely to want to
 * check, and checking it meant knowing that the filters panel hides a source
 * list. With eleven sources, "is this one earning its place" stopped being a
 * question anybody could answer from the screen that raises it.
 *
 * The Dashboard case below is the one that matters: the panel rendering
 * correctly proves nothing if the button that reaches it sends the wrong
 * filters. It asserts the request that actually went to the server.
 */

const tendersMock = vi.fn();
const sourcesMock = vi.fn();
const statsMock = vi.fn();

vi.mock('../api/client', () => ({
  ApiError: class ApiError extends Error {
    status: number;
    constructor(message: string, status: number) {
      super(message);
      this.status = status;
    }
  },
  api: {
    tenders: (...args: unknown[]) => tendersMock(...args),
    sources: (...args: unknown[]) => sourcesMock(...args),
    stats: (...args: unknown[]) => statsMock(...args),
    fetchRuns: vi.fn().mockResolvedValue([]),
    automation: vi.fn().mockResolvedValue(null),
    fetchNow: vi.fn(),
    rescore: vi.fn(),
    tender: vi.fn(),
    translate: vi.fn(),
    setFeedback: vi.fn(),
    clearFeedback: vi.fn(),
    setCredential: vi.fn(),
    learnedPatterns: vi.fn().mockResolvedValue({ phrases: [] }),
    matchingRules: vi.fn().mockResolvedValue({}),
  },
}));

let container: HTMLDivElement;
let root: Root;

function source(over: Record<string, unknown> = {}) {
  return {
    name: 'spend_network',
    display_name: 'Spend Network (Open Opportunities)',
    homepage: 'https://www.spendnetwork.com',
    enabled: true,
    requires_api_key: true,
    credential_configured: true,
    credential_hint: '…9876',
    unavailable_reason: null,
    keyword_prefiltered: false,
    notes: 'An aggregator: one account covers ~40 national portals.',
    tender_count: 412,
    running: false,
    last_status: 'success',
    last_run_at: '2026-09-15T04:00:00',
    last_success_at: '2026-09-15T04:00:00',
    last_error: null,
    ...over,
  };
}

beforeEach(() => {
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
  tendersMock.mockReset();
  sourcesMock.mockReset();
  statsMock.mockReset();
  window.history.replaceState(null, '', '/');
  window.localStorage.clear();
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
});

function click(node: Element) {
  act(() => {
    node.dispatchEvent(new MouseEvent('click', { bubbles: true }));
  });
}

function buttonSaying(text: string): HTMLButtonElement | undefined {
  return [...container.querySelectorAll('button')].find((b) => b.textContent?.includes(text));
}

describe('the source panel', () => {
  async function render(over: Record<string, unknown> = {}, props: Record<string, unknown> = {}) {
    const { SourceFocus } = await import('./SourceFocus');
    await act(async () => {
      root.render(
        <SourceFocus
          source={source(over) as never}
          shown={412}
          busySource={null}
          onFetch={vi.fn()}
          onClear={vi.fn()}
          {...props}
        />,
      );
    });
  }

  it('names the source and what it has stored', async () => {
    await render();
    expect(container.textContent).toContain('Spend Network (Open Opportunities)');
    expect(container.textContent).toContain('412');
    expect(container.textContent).toContain('notices stored from this source');
    expect(container.textContent).toContain('An aggregator');
  });

  it('separates what is stored from what the filters admit', async () => {
    await render({}, { shown: 7 });
    expect(container.textContent).toContain('412');
    expect(container.textContent).toContain('7');
    expect(container.textContent).toContain('shown under the filters on screen');
  });

  it('says nothing about filters when they are admitting everything', async () => {
    // Otherwise an unfiltered view reads as a filtered one.
    await render({}, { shown: 412 });
    expect(container.textContent).not.toContain('shown under the filters on screen');
  });

  it('distinguishes a source that has never run from one that found nothing', async () => {
    await render({ tender_count: 0, last_run_at: null, last_success_at: null, last_status: null });
    expect(container.textContent).toContain('Nothing stored from this source yet');
    expect(container.textContent).toContain('never queried');
  });

  it('will not offer to fetch a source that cannot run', async () => {
    await render({ unavailable_reason: 'SPEND_NETWORK_EMAIL is not set' });
    expect(container.textContent).toContain('SPEND_NETWORK_EMAIL is not set');
    expect(buttonSaying('Fetch this source')?.disabled).toBe(true);
  });

  it('carries the health colour the card used, so arriving here is continuous', async () => {
    await render({ last_status: 'failed' });
    expect(container.querySelector('.srcfocus')?.className).toContain('src--critical');
  });

  it('hands back the whole list', async () => {
    const onClear = vi.fn();
    await render({}, { onClear });
    click(buttonSaying('All sources')!);
    expect(onClear).toHaveBeenCalled();
  });
});

describe('which source heads the list', () => {
  it('is the one selected', async () => {
    const { focusedSource } = await import('./SourceFocus');
    const all = [source(), source({ name: 'ted' })] as never[];
    expect(focusedSource(all, ['ted'])?.name).toBe('ted');
  });

  it('is nobody when two are selected', async () => {
    // The panel states one source's stored count beside the rows on screen.
    // With two selected that sentence is false about both of them.
    const { focusedSource } = await import('./SourceFocus');
    const all = [source(), source({ name: 'ted' })] as never[];
    expect(focusedSource(all, ['ted', 'spend_network'])).toBeNull();
  });

  it('is nobody when none is selected', async () => {
    const { focusedSource } = await import('./SourceFocus');
    expect(focusedSource([source()] as never[], [])).toBeNull();
  });

  it('is nobody when the URL names a source that is gone', async () => {
    // A shared link can outlive a source that was removed from the registry.
    const { focusedSource } = await import('./SourceFocus');
    expect(focusedSource([source()] as never[], ['a_deleted_source'])).toBeNull();
  });
});

describe('opening a source from the dashboard', () => {
  /** The debounce between a filter change and the request is a real timer. */
  async function settle() {
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
  }

  async function renderDashboard(
    sources = [source(), source({ name: 'ted', display_name: 'TED', tender_count: 90 })],
  ) {
    statsMock.mockResolvedValue({
      total_tenders: 900,
      hidden_total: 40,
      categories: [],
      score_bands: { good_fit: 70, possible_fit: 50 },
    });
    sourcesMock.mockResolvedValue(sources);
    tendersMock.mockResolvedValue({ items: [], total: 0, page: 1, pages: 1, page_size: 25 });

    const { Dashboard } = await import('../pages/Dashboard');
    await act(async () => {
      root.render(
        <Dashboard
          auth={{ signOut: vi.fn() } as never}
          user={
            {
              id: 1,
              email: 'a@b.c',
              display_name: 'A Reader',
              role: 'admin',
              is_active: true,
            } as never
          }
        />,
      );
    });
    await settle();
  }

  it('asks the server for everything that source ever brought', async () => {
    await renderDashboard();
    // The health strip is collapsed on arrival; the cards live behind it.
    click(container.querySelector('.sources__bar')!);
    tendersMock.mockClear();
    click(buttonSaying('See its notices')!);
    await settle();

    const sent = tendersMock.mock.calls.at(-1)?.[0];
    expect(sent.sources).toEqual(['spend_network']);
    // The "All tenders" lens, not the default view. The default hides anything
    // under 70 points, anything closed and anything marked not relevant - which
    // is most of what somebody checking a suspect source came to look at.
    expect(sent.minimum_score).toBe(0);
    expect(sent.active_only).toBe(false);
    expect(sent.hidden).toBeNull();
  });

  it('puts the source in the URL, so the view can be shared', async () => {
    await renderDashboard();
    click(container.querySelector('.sources__bar')!);
    click(buttonSaying('See its notices')!);
    await settle();
    expect(window.location.search).toContain('sources=spend_network');
  });

  it('shows the source panel once one is selected, and not before', async () => {
    await renderDashboard();
    expect(container.querySelector('.srcfocus')).toBeNull();
    click(container.querySelector('.sources__bar')!);
    click(buttonSaying('See its notices')!);
    await settle();
    expect(container.querySelector('.srcfocus')).not.toBeNull();
    expect(container.querySelector('.srcfocus')?.textContent).toContain('Spend Network');
  });

  it('offers no way in for a source that has stored nothing', async () => {
    await renderDashboard([source({ tender_count: 0 })]);
    click(container.querySelector('.sources__bar')!);
    expect(buttonSaying('See its notices')?.disabled).toBe(true);
  });
});
