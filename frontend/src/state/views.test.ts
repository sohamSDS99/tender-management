import { describe, expect, it } from 'vitest';
import { VIEWS, activeView, type ViewContext } from './views';
import { DEFAULT_FILTERS } from './urlFilters';
import type { TenderFilters } from '../types';

const ctx: ViewContext = { lastRunAt: '2026-08-21T06:00:00Z', goodFitBand: 70 };

const apply = (key: string, context: ViewContext = ctx): TenderFilters => {
  const view = VIEWS.find((v) => v.key === key)!;
  return { ...DEFAULT_FILTERS, ...view.patch(context) };
};

describe('views', () => {
  it('offers exactly the four questions a bidder arrives with', () => {
    expect(VIEWS.map((v) => v.key)).toEqual(['attention', 'new', 'closing', 'all']);
  });

  it('needs-attention uses the engine’s own good-fit band, not a magic number', () => {
    expect(apply('attention').minimum_score).toBe(70);
    expect(apply('attention', { ...ctx, goodFitBand: 65 }).minimum_score).toBe(65);
    expect(apply('attention').active_only).toBe(true);
  });

  it('new filters on discovery time from the last sweep', () => {
    expect(apply('new').first_seen_from).toBe('2026-08-21T06:00:00Z');
    expect(apply('new').sort).toBe('score_desc');
  });

  it('new degrades to no filter when nothing has swept yet', () => {
    expect(apply('new', { ...ctx, lastRunAt: null }).first_seen_from).toBe('');
  });

  it('closing-soon is a two-week window sorted by deadline', () => {
    const filters = apply('closing');
    expect(filters.sort).toBe('deadline_asc');
    expect(filters.deadline_from).toMatch(/^\d{4}-\d{2}-\d{2}$/);
    const days =
      (new Date(filters.deadline_to).getTime() - new Date(filters.deadline_from).getTime()) /
      86_400_000;
    expect(Math.round(days)).toBe(14);
  });

  it('all removes every narrowing filter', () => {
    const filters = apply('all');
    expect(filters.minimum_score).toBe(0);
    expect(filters.active_only).toBe(false);
  });
});

describe('activeView', () => {
  it('lights the tab whose preset the filters match', () => {
    for (const view of VIEWS) {
      expect(activeView(apply(view.key), ctx)).toBe(view.key);
    }
  });

  it('lights nothing once the user has made their own mix', () => {
    const custom = { ...apply('attention'), minimum_score: 42 };
    expect(activeView(custom, ctx)).toBeNull();
  });

  it('ignores fields a view does not own, so a search keeps the tab lit', () => {
    const searched = { ...apply('attention'), query: 'safety data sheets' };
    expect(activeView(searched, ctx)).toBe('attention');
  });

  it('does not confuse two views that differ only by sort', () => {
    const attention = apply('attention');
    expect(activeView({ ...attention, sort: 'deadline_asc' }, ctx)).not.toBe('attention');
  });
});
