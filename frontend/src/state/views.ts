import type { TenderFilters } from '../types';
import { DEFAULT_FILTERS } from './urlFilters';

/**
 * The four questions a bidder actually arrives with.
 *
 * These replace the five metric tiles the previous interface led with. A tile
 * that says "320 stored" answers a question nobody has; "what needs my attention"
 * is the reason someone opened the page.
 *
 * A view is a filter preset, not separate state - so there is one source of
 * truth. `activeView` reads the current filters back to decide which tab is lit,
 * and any hand-edit that no longer matches a preset simply lights none.
 */
export type ViewKey = 'attention' | 'new' | 'closing' | 'all';

export interface ViewContext {
  /** Start of the most recent sweep, from /api/automation. */
  lastRunAt: string | null;
  /** The engine's own "good fit" band, from /api/stats. */
  goodFitBand: number;
}

const day = (offset: number): string =>
  new Date(Date.now() + offset * 86_400_000).toISOString().slice(0, 10);

export interface ViewSpec {
  key: ViewKey;
  label: string;
  /** Shown under the label when the view is unavailable. */
  unavailable?: string;
  patch: (ctx: ViewContext) => Partial<TenderFilters>;
}

export const VIEWS: ViewSpec[] = [
  {
    key: 'attention',
    label: 'Needs attention',
    patch: (ctx) => ({
      ...DEFAULT_FILTERS,
      minimum_score: ctx.goodFitBand,
      active_only: true,
      sort: 'score_desc',
    }),
  },
  {
    key: 'new',
    label: 'New',
    unavailable: 'no run yet',
    patch: (ctx) => ({
      ...DEFAULT_FILTERS,
      minimum_score: 0,
      active_only: true,
      first_seen_from: ctx.lastRunAt ?? '',
      sort: 'score_desc',
    }),
  },
  {
    key: 'closing',
    label: 'Closing soon',
    patch: () => ({
      ...DEFAULT_FILTERS,
      minimum_score: 40,
      active_only: true,
      deadline_from: day(0),
      deadline_to: day(14),
      sort: 'deadline_asc',
    }),
  },
  {
    key: 'all',
    label: 'All',
    patch: () => ({
      ...DEFAULT_FILTERS,
      minimum_score: 0,
      active_only: false,
      sort: 'first_seen_desc',
    }),
  },
];

/** Fields a view controls. Anything else the user set is theirs to keep. */
export const OWNED: (keyof TenderFilters)[] = [
  'minimum_score',
  'maximum_score',
  'active_only',
  'first_seen_from',
  'deadline_from',
  'deadline_to',
  'sort',
  'fit_statuses',
  'deployment_fits',
];

function sameValue(a: unknown, b: unknown): boolean {
  if (Array.isArray(a) && Array.isArray(b)) {
    return a.length === b.length && a.every((v) => (b as unknown[]).includes(v));
  }
  return a === b;
}

/** Which tab is lit, or null when the filters are the user's own mix. */
export function activeView(filters: TenderFilters, ctx: ViewContext): ViewKey | null {
  for (const view of VIEWS) {
    const wanted = view.patch(ctx) as Record<string, unknown>;
    const matches = OWNED.every((key) => {
      if (!(key in wanted)) return true;
      return sameValue(wanted[key], filters[key]);
    });
    if (matches) return view.key;
  }
  return null;
}
