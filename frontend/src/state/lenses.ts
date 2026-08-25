import type { Stats, TenderFilters } from '../types';
import { DEFAULT_FILTERS } from './urlFilters';

/**
 * The six lenses in the sidebar.
 *
 * A lens is a filter preset, not separate state, so there is one source of
 * truth: `activeLens` reads the current filters back to decide which item is
 * lit, and a hand-edit that matches no preset simply lights none.
 *
 * They overlap by construction — one tender is Open and Top scoring and
 * Closing soon at once — so the counts do not sum to the total. That is why
 * the sidebar renders them as muted badges rather than headline numerals.
 *
 * This replaces the old split between VIEWS (tabs) and TILES (the stat row),
 * which were two vocabularies for the same operation.
 */
export type LensKey = 'new' | 'open' | 'topscoring' | 'closing' | 'review' | 'all';

export interface LensContext {
  /** Start of the most recent sweep, from /api/automation. */
  lastRunAt: string | null;
  /** The engine's own "good fit" band, from /api/stats. */
  goodFitBand: number;
  /** The engine's own "possible fit" band, from /api/stats. */
  possibleFitBand: number;
}

/**
 * A day boundary in Dhaka, not in UTC.
 *
 * Built from toISOString() this rolled over at 06:00 Dhaka — mid-morning for
 * the bid team — which silently unlit the Closing-soon lens on a page they had
 * left open, because activeLens recomputes the preset on every render.
 */
export const day = (offset: number): string =>
  new Intl.DateTimeFormat('en-CA', {
    timeZone: 'Asia/Dhaka',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
  }).format(new Date(Date.now() + offset * 86_400_000));

export interface LensSpec {
  key: LensKey;
  label: string;
  /** Dot colour beside the label. Never the only carrier of the meaning. */
  tone: 'brand' | 'good' | 'warning' | 'serious' | 'none';
  /** Shown under the label when the lens cannot be used. */
  unavailable?: string;
  /** One sentence explaining what this lens actually contains. */
  note: (ctx: LensContext) => string;
  /**
   * The locked chip's text: the lens's real predicate, not a fixed string.
   * A hardcoded "70" would go stale the moment the band is tuned in
   * Matching rules, so the band is read from context every render.
   */
  lockedLabel: (ctx: LensContext) => string | null;
  patch: (ctx: LensContext) => Partial<TenderFilters>;
  /**
   * Only where /api/stats counts exactly the population this lens filters on.
   * "New this fetch" has no such stat, so it shows no number rather than a
   * guess — a count that disagrees with the list it opens is worse than none.
   */
  count: (stats: Stats | null) => number | null;
}

export const LENSES: LensSpec[] = [
  {
    key: 'new',
    label: 'New this fetch',
    tone: 'brand',
    unavailable: 'no run yet',
    note: () =>
      'Discovered by the most recent sweep, at any score. Nothing here has been seen before.',
    lockedLabel: () => 'Found by the last sweep',
    patch: (ctx) => ({
      ...DEFAULT_FILTERS,
      minimum_score: 0,
      active_only: false,
      first_seen_from: ctx.lastRunAt ?? '',
      sort: 'score_desc',
    }),
    count: () => null,
  },
  {
    key: 'open',
    label: 'Open opportunities',
    tone: 'brand',
    note: () => 'Still accepting bids, at any score.',
    lockedLabel: () => 'Open opportunities only',
    patch: () => ({
      ...DEFAULT_FILTERS,
      minimum_score: 0,
      active_only: true,
      sort: 'score_desc',
    }),
    count: (stats) => stats?.actionable ?? null,
  },
  {
    key: 'topscoring',
    label: 'Top scoring',
    tone: 'good',
    note: (ctx) => `Scored ${ctx.goodFitBand} or higher and still open.`,
    lockedLabel: (ctx) => `Score ≥ ${ctx.goodFitBand}`,
    patch: (ctx) => ({
      ...DEFAULT_FILTERS,
      minimum_score: ctx.goodFitBand,
      active_only: true,
      sort: 'score_desc',
    }),
    count: (stats) => stats?.good_fit_or_better ?? null,
  },
  {
    key: 'closing',
    label: 'Closing soon',
    tone: 'warning',
    note: () => 'Deadline within fourteen days, soonest first.',
    lockedLabel: () => 'Closing within 14 days',
    patch: () => ({
      ...DEFAULT_FILTERS,
      minimum_score: 0,
      active_only: true,
      deadline_from: day(0),
      deadline_to: day(14),
      sort: 'deadline_asc',
    }),
    count: (stats) => stats?.closing_soon ?? null,
  },
  {
    key: 'review',
    label: 'Needs review',
    tone: 'serious',
    note: () => 'Ambiguous fit or hosting — the engine wants a human call.',
    lockedLabel: () => 'Fit status: needs review',
    patch: () => ({
      ...DEFAULT_FILTERS,
      minimum_score: 0,
      active_only: true,
      fit_statuses: ['manual_review'],
      sort: 'score_desc',
    }),
    count: (stats) => stats?.possible_or_review ?? null,
  },
  {
    key: 'all',
    label: 'All tenders',
    tone: 'none',
    note: () =>
      'Everything ever ingested, newest discovery first. Nothing is discarded, so a notice that scored badly can still be found and the profile corrected.',
    lockedLabel: () => null,
    patch: () => ({
      ...DEFAULT_FILTERS,
      minimum_score: 0,
      active_only: false,
      sort: 'first_seen_desc',
    }),
    count: (stats) => stats?.total_tenders ?? null,
  },
];

export function lensByKey(key: LensKey | null): LensSpec | undefined {
  return key === null ? undefined : LENSES.find((lens) => lens.key === key);
}

/**
 * Fields a lens controls. Anything else the reader set is theirs to keep.
 *
 * Note the absence of 'sort'. Ordering belongs to the reader and is orthogonal
 * to which question they are asking, so changing it must not silently unlight
 * the lens and conjure chips for filters they never set.
 */
export const OWNED: (keyof TenderFilters)[] = [
  'minimum_score',
  'maximum_score',
  'active_only',
  'first_seen_from',
  'deadline_from',
  'deadline_to',
  'fit_statuses',
  'deployment_fits',
];

function sameValue(a: unknown, b: unknown): boolean {
  if (Array.isArray(a) && Array.isArray(b)) {
    return a.length === b.length && a.every((v) => (b as unknown[]).includes(v));
  }
  return a === b;
}

/**
 * Whether a lens can narrow anything given the current context.
 *
 * "New this fetch" before the first sweep has no first_seen_from to filter on,
 * which makes its preset identical to "All tenders". Left in the running it
 * would win on order and label the whole corpus as newly found.
 */
function usable(lens: LensSpec, ctx: LensContext): boolean {
  return lens.key === 'new' ? ctx.lastRunAt !== null : true;
}

/** Which lens is lit, or null when the filters are the reader's own mix. */
export function activeLens(filters: TenderFilters, ctx: LensContext): LensKey | null {
  for (const lens of LENSES) {
    if (!usable(lens, ctx)) continue;
    const wanted = lens.patch(ctx) as Record<string, unknown>;
    const matches = OWNED.every((key) => {
      if (!(key in wanted)) return true;
      return sameValue(wanted[key], filters[key]);
    });
    if (matches) return lens.key;
  }
  return null;
}
