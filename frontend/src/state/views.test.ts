import { describe, expect, it } from 'vitest';
import { VIEWS, activeView, type ViewContext } from './views';
import { DEFAULT_FILTERS } from './urlFilters';
import type { TenderFilters } from '../types';

const ctx: ViewContext = {
  lastRunAt: '2026-08-21T06:00:00Z',
  goodFitBand: 70,
  possibleFitBand: 50,
};

const apply = (key: string, context: ViewContext = ctx): TenderFilters => {
  const view = VIEWS.find((v) => v.key === key)!;
  return { ...DEFAULT_FILTERS, ...view.patch(context) };
};

describe('views', () => {
  it('offers the four buckets everything stored falls into', () => {
    expect(VIEWS.map((v) => v.key)).toEqual(['new', 'relevant', 'irrelevant', 'all']);
  });

  it('new filters on discovery time from the last sweep, at any score', () => {
    expect(apply('new').first_seen_from).toBe('2026-08-21T06:00:00Z');
    expect(apply('new').minimum_score).toBe(0);
    expect(apply('new').sort).toBe('score_desc');
  });

  it('new degrades to no filter when nothing has swept yet', () => {
    expect(apply('new', { ...ctx, lastRunAt: null }).first_seen_from).toBe('');
  });

  it("relevant uses the engine's possible-fit band, not a literal", () => {
    expect(apply('relevant').minimum_score).toBe(50);
    expect(apply('relevant', { ...ctx, possibleFitBand: 45 }).minimum_score).toBe(45);
  });

  it('the score buckets filter on score alone, so /api/stats counts them exactly', () => {
    // /api/stats counts these bands purely on relevance_score. Any extra filter
    // here would put a count beside a tab that disagrees with the list it opens.
    for (const key of ['relevant', 'irrelevant']) {
      const filters = apply(key);
      expect(filters.active_only).toBe(false);
      expect(filters.fit_statuses).toEqual([]);
      expect(filters.deployment_fits).toEqual([]);
    }
  });

  it('irrelevant is the exact complement of relevant, with no gap or overlap', () => {
    // If these two ever both include a score, or both exclude one, the tab counts
    // stop adding up to the stored total and the "nothing is discarded" promise
    // on the page becomes false.
    const relevantMin = apply('relevant').minimum_score;
    const irrelevantMax = apply('irrelevant').maximum_score;
    expect(irrelevantMax).toBe(relevantMin - 1);
  });

  it('irrelevant still floors at zero when the band is zero', () => {
    expect(apply('irrelevant', { ...ctx, possibleFitBand: 0 }).maximum_score).toBe(0);
  });

  it('all removes every narrowing filter', () => {
    const filters = apply('all');
    expect(filters.minimum_score).toBe(0);
    expect(filters.active_only).toBe(false);
  });

  it('every bucket explains itself in a sentence', () => {
    for (const view of VIEWS) {
      expect(view.note(ctx).length).toBeGreaterThan(20);
    }
  });
});

describe('activeView', () => {
  it('lights the tab whose preset the filters match', () => {
    for (const view of VIEWS) {
      expect(activeView(apply(view.key), ctx)).toBe(view.key);
    }
  });

  it('lights nothing once the user has made their own mix', () => {
    const custom = { ...apply('relevant'), minimum_score: 42 };
    expect(activeView(custom, ctx)).toBeNull();
  });

  it('ignores fields a view does not own, so a search keeps the tab lit', () => {
    const searched = { ...apply('relevant'), query: 'safety data sheets' };
    expect(activeView(searched, ctx)).toBe('relevant');
  });

  it('keeps the tab lit when only the sort changes', () => {
    // Ordering belongs to the reader. Treating it as owned by the view unlit the
    // tab and conjured chips for filters nobody set.
    const relevant = apply('relevant');
    expect(activeView({ ...relevant, sort: 'deadline_asc' }, ctx)).toBe('relevant');
  });
});
