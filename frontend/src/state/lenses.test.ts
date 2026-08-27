import { describe, expect, it } from 'vitest';
import { LENSES, activeLens, lensByKey, type LensContext } from './lenses';
import { DEFAULT_FILTERS } from './urlFilters';
import type { TenderFilters } from '../types';

const ctx: LensContext = {
  lastRunAt: '2026-08-24T15:44:00Z',
  goodFitBand: 70,
  possibleFitBand: 50,
};

const withLens = (key: string, c: LensContext = ctx): TenderFilters =>
  ({ ...DEFAULT_FILTERS, ...lensByKey(key as never)!.patch(c) }) as TenderFilters;

describe('activeLens', () => {
  it('lights each lens from its own preset', () => {
    for (const lens of LENSES) {
      expect(activeLens(withLens(lens.key), ctx)).toBe(lens.key);
    }
  });

  it('does not own sort, so re-ordering keeps the lens lit', () => {
    const filters = { ...withLens('topscoring'), sort: 'deadline_asc' } as TenderFilters;
    expect(activeLens(filters, ctx)).toBe('topscoring');
  });

  it('lights nothing when the reader has their own mix', () => {
    const filters = { ...DEFAULT_FILTERS, minimum_score: 37, active_only: true } as TenderFilters;
    expect(activeLens(filters, ctx)).toBeNull();
  });

  it('does not let "new" shadow "all" before the first sweep', () => {
    // With no run, "new" has no first_seen_from to narrow on, so its preset is
    // indistinguishable from "all". It must stand down rather than mislabel
    // the whole corpus as newly found.
    const noRun: LensContext = { ...ctx, lastRunAt: null };
    expect(activeLens(withLens('all', noRun), noRun)).toBe('all');
  });
});

describe('lockedLabel', () => {
  it('renders the band from context, not a hardcoded number', () => {
    const lens = lensByKey('topscoring')!;
    expect(lens.lockedLabel(ctx)).toBe('Score ≥ 70');
    expect(lens.lockedLabel({ ...ctx, goodFitBand: 85 })).toBe('Score ≥ 85');
  });

  it('gives the audit root no locked chip, because it narrows nothing', () => {
    expect(lensByKey('all')!.lockedLabel(ctx)).toBeNull();
  });
});

describe('counts', () => {
  it('takes the new-lens count from the sweep that created the rows', () => {
    // Not from /api/stats: the count must equal the list it opens, and
    // records_created is exactly the population first_seen_from selects.
    const automation = { last_run: { records_created: 12 } } as never;
    expect(lensByKey('new')!.count(null, automation)).toBe(12);
  });

  it('gives "new" no count rather than a guess when no sweep has run', () => {
    expect(lensByKey('new')!.count(null, null)).toBeNull();
  });

  it('counts the Not-relevant lens with the same clause the list filters on', () => {
    const stats = { total_tenders: 300, hidden_total: 42 } as never;
    expect(lensByKey('notrelevant')!.count(stats, null)).toBe(42);
  });

  it('adds the hidden back for "All tenders", because /api/stats leaves them out', () => {
    // total_tenders excludes hidden notices so every other lens badge equals
    // the list it opens. This lens is the one that shows them, so its badge has
    // to add them back or it would under-report the corpus it displays.
    const stats = { total_tenders: 300, hidden_total: 42 } as never;
    expect(lensByKey('all')!.count(stats, null)).toBe(342);
  });
});

describe('the Not-relevant lens', () => {
  it('is the only lens that asks for hidden notices', () => {
    const asking = LENSES.filter((lens) => lens.patch(ctx).hidden === true);
    expect(asking.map((lens) => lens.key)).toEqual(['notrelevant']);
  });

  it('is distinguishable from "All tenders", which asks for both', () => {
    // Both drop the score floor and the open-only filter, so `hidden` is the
    // only thing separating them. Without it in OWNED the first one in the list
    // would light for both presets.
    expect(lensByKey('all')!.patch(ctx).hidden).toBeNull();
    expect(activeLens(withLens('notrelevant'), ctx)).toBe('notrelevant');
    expect(activeLens(withLens('all'), ctx)).toBe('all');
  });

  it('hides them from every lens that is a work queue', () => {
    for (const lens of LENSES) {
      // 'all' is the audit root and 'new' is a report on one sweep; both must
      // show everything. Every other lens is somewhere work gets done.
      if (['notrelevant', 'all', 'new'].includes(lens.key)) continue;
      expect(lens.patch(ctx).hidden).toBe(false);
    }
  });

  it('does not let hiding break the New-lens count', () => {
    // Its badge is the sweep's own records_created — every row that batch
    // inserted, hidden or not — so the list it opens has to include them or the
    // number beside it disagrees with itself.
    expect(lensByKey('new')!.patch(ctx).hidden).toBeNull();
  });
});
