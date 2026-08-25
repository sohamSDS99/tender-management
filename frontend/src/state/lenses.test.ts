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
});
