import type { AutomationStatus, Stats, TenderFilters } from '../types';
import { DEFAULT_FILTERS } from './urlFilters';

/**
 * The seven lenses in the sidebar.
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
export type LensKey = 'new' | 'open' | 'topscoring' | 'closing' | 'review' | 'all' | 'notrelevant';

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
  count: (stats: Stats | null, automation: AutomationStatus | null) => number | null;
}

export const LENSES: LensSpec[] = [
  {
    key: 'new',
    label: 'New this fetch',
    tone: 'brand',
    unavailable: 'no run yet',
    note: () =>
      'Everything the most recent sweep discovered, at any score — including anything already hidden as not relevant, which is marked as such. Nothing here has been seen before.',
    lockedLabel: () => 'Found by the last sweep',
    patch: (ctx) => ({
      ...DEFAULT_FILTERS,
      minimum_score: 0,
      active_only: false,
      // The one working lens that does *not* hide rejects, and it has to be.
      // Its count is the sweep's own `records_created` — every row that batch
      // inserted, unconditionally — so hiding some of them would put a number
      // beside a lens that disagrees with itself, which is the failure the
      // count-equals-list rule exists to prevent. This lens is a report on a
      // sweep rather than a work queue; the queues are the lenses below it.
      hidden: null,
      first_seen_from: ctx.lastRunAt ?? '',
      sort: 'score_desc',
    }),
    // The last sweep's own created-count. It satisfies the rule above by
    // construction rather than coincidence: this lens filters on
    // `first_seen_from = <that batch's start>`, and records_created is exactly
    // the number of rows that batch inserted.
    count: (_stats, automation) => automation?.last_run?.records_created ?? null,
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
      'Everything ever ingested, newest discovery first — including what was marked not relevant. Nothing is discarded, so a notice that scored badly can still be found and the profile corrected.',
    lockedLabel: () => null,
    patch: () => ({
      ...DEFAULT_FILTERS,
      minimum_score: 0,
      active_only: false,
      // The one lens that means *everything*. Leaving the default in place here
      // would make the label a lie, and this is where someone goes precisely
      // when a notice they expected is nowhere else to be found.
      hidden: null,
      sort: 'first_seen_desc',
    }),
    count: (stats) => (stats === null ? null : stats.total_tenders + stats.hidden_total),
  },
  {
    // Last, because it is the discard pile — but present, because a learning
    // system that cannot be audited is one nobody will trust. Every mark is
    // reversible and this is the only screen from which to reverse one.
    key: 'notrelevant',
    label: 'Not relevant',
    tone: 'none',
    note: () =>
      'Marked not relevant by a reviewer, or matched to those by the patterns learned from them. Open one to see why, and to put it back.',
    lockedLabel: () => 'Hidden as not relevant',
    patch: () => ({
      ...DEFAULT_FILTERS,
      minimum_score: 0,
      active_only: false,
      hidden: true,
      sort: 'first_seen_desc',
    }),
    // Exactly the population this lens filters on: /api/stats counts it with
    // the same clause the tender list uses, and excludes it from every other
    // count for the same reason.
    count: (stats) => stats?.hidden_total ?? null,
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
  // Owned, because it is what separates 'All tenders' from 'Not relevant' —
  // without it here those two lenses are indistinguishable and the first one
  // in the list wins both.
  'hidden',
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
