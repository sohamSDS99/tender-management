import type { TenderFilters } from '../types';
import { DEFAULT_FILTERS } from './urlFilters';

/**
 * The four buckets everything stored falls into.
 *
 * A bucket is a filter preset, not separate state - so there is one source of
 * truth. `activeView` reads the current filters back to decide which tab is lit,
 * and any hand-edit that no longer matches a preset simply lights none.
 *
 * "Irrelevant" is deliberately reachable. Hundreds of notices are scored below
 * the bar every sweep and the previous tabs hid them completely, which meant a
 * false negative - the notice that mattered and scored 30 - was invisible and
 * therefore unfixable. Nothing is discarded; the bucket says so on the page.
 */
export type ViewKey = 'new' | 'relevant' | 'irrelevant' | 'all';

export interface ViewContext {
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
 * Built from toISOString() this rolled over at 06:00 Dhaka — mid-morning for the
 * bid team — which silently unlit the Closing-soon tab on a page they had left
 * open, because activeView recomputes the preset on every render.
 */
export const day = (offset: number): string =>
  new Intl.DateTimeFormat('en-CA', {
    timeZone: 'Asia/Dhaka',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
  }).format(new Date(Date.now() + offset * 86_400_000));

export interface ViewSpec {
  key: ViewKey;
  label: string;
  /** Dot colour beside the label. Never the only carrier of the meaning. */
  tone: 'brand' | 'good' | 'critical' | 'none';
  /** Shown under the label when the view is unavailable. */
  unavailable?: string;
  /** One sentence on the page explaining what the bucket actually contains. */
  note: (ctx: ViewContext) => string;
  patch: (ctx: ViewContext) => Partial<TenderFilters>;
}

export const VIEWS: ViewSpec[] = [
  {
    key: 'new',
    label: 'New this fetch',
    tone: 'brand',
    unavailable: 'no run yet',
    note: () =>
      'Discovered by the most recent sweep, at any score. Nothing here has been seen before.',
    patch: (ctx) => ({
      ...DEFAULT_FILTERS,
      minimum_score: 0,
      active_only: false,
      first_seen_from: ctx.lastRunAt ?? '',
      sort: 'score_desc',
    }),
  },
  {
    key: 'relevant',
    label: 'Relevant',
    tone: 'good',
    note: (ctx) =>
      `Scored ${ctx.possibleFitBand} or higher. These are the notices worth a human read — use “Open opportunities only” in Settings to drop the ones that have closed.`,
    patch: (ctx) => ({
      ...DEFAULT_FILTERS,
      // The band the engine uses, not a literal, and deliberately no active_only:
      // the count beside this tab comes from /api/stats, which counts purely on
      // score. Adding a filter here that the count does not apply would put a
      // number next to a tab that disagrees with the list the tab opens.
      minimum_score: ctx.possibleFitBand,
      // Explicit, not inherited: DEFAULT_FILTERS turns this on, and /api/stats
      // counts these bands regardless of whether a notice is still open.
      active_only: false,
      sort: 'score_desc',
    }),
  },
  {
    key: 'irrelevant',
    label: 'Irrelevant',
    tone: 'critical',
    note: (ctx) =>
      `Scored below ${ctx.possibleFitBand}. Kept and searchable so a wrong score can be found and the profile corrected.`,
    patch: (ctx) => ({
      ...DEFAULT_FILTERS,
      minimum_score: 0,
      // Inclusive bound, so this and Relevant partition the set with no notice
      // falling into both or neither.
      maximum_score: Math.max(0, ctx.possibleFitBand - 1),
      active_only: false,
      sort: 'score_desc',
    }),
  },
  {
    key: 'all',
    label: 'All stored',
    tone: 'none',
    note: () => 'Everything ever ingested, newest discovery first.',
    patch: () => ({
      ...DEFAULT_FILTERS,
      minimum_score: 0,
      active_only: false,
      sort: 'first_seen_desc',
    }),
  },
];

/**
 * The summary tiles across the top. Each one is a filter, not a decoration —
 * clicking it narrows the list to exactly the population it counted, which is the
 * only way a number on a dashboard can be checked.
 */
export type TileKey = 'open' | 'topscoring' | 'closing' | 'review' | 'failed';

export interface TileSpec {
  key: TileKey;
  label: string;
  tone: 'brand' | 'good' | 'warning' | 'serious' | 'critical';
  patch: (ctx: ViewContext) => Partial<TenderFilters>;
}

export const TILES: TileSpec[] = [
  {
    key: 'open',
    label: 'Open opportunities',
    tone: 'brand',
    patch: () => ({ ...DEFAULT_FILTERS, minimum_score: 0, active_only: true, sort: 'score_desc' }),
  },
  {
    key: 'topscoring',
    label: 'Top scoring',
    tone: 'good',
    patch: (ctx) => ({
      ...DEFAULT_FILTERS,
      minimum_score: ctx.goodFitBand,
      active_only: true,
      sort: 'score_desc',
    }),
  },
  {
    key: 'closing',
    label: 'Closing ≤ 14 days',
    tone: 'warning',
    patch: () => ({
      ...DEFAULT_FILTERS,
      minimum_score: 0,
      active_only: true,
      deadline_from: day(0),
      deadline_to: day(14),
      sort: 'deadline_asc',
    }),
  },
  {
    key: 'review',
    label: 'Needs review',
    tone: 'serious',
    patch: () => ({
      ...DEFAULT_FILTERS,
      minimum_score: 0,
      active_only: true,
      fit_statuses: ['manual_review'],
      sort: 'score_desc',
    }),
  },
];

/** Fields a view controls. Anything else the user set is theirs to keep. */
export // Note the absence of 'sort'. Ordering belongs to the reader and is orthogonal
// to which question they are asking, so changing it must not silently unlight the
// tab and conjure chips for filters they never set.
const OWNED: (keyof TenderFilters)[] = [
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
