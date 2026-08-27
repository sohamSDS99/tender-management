import type { DeploymentFit, FitStatus, SortOption, TenderFilters } from '../types';

/**
 * The filter set lives in the URL.
 *
 * Two things depend on this being exact:
 *  - a shared or refreshed link must restore the same result set (delta 10);
 *  - the Slack digest links here. `notifier.digest_permalink` emits
 *    `?minimum_score=<n>&active_only=true&sort=first_seen_desc`, and every
 *    tender entry emits `?tender=<id>` - so those names are a contract with the
 *    backend, not an internal detail. The parameter names deliberately match the
 *    API's own query parameters, which makes a dashboard URL and an API URL
 *    trivially comparable when something looks wrong.
 */

export const SORT_OPTIONS: { value: SortOption; label: string }[] = [
  { value: 'score_desc', label: 'Relevance — high first' },
  { value: 'deadline_asc', label: 'Deadline — soonest first' },
  { value: 'published_desc', label: 'Published — newest first' },
  { value: 'first_seen_desc', label: 'Recently discovered' },
  { value: 'score_asc', label: 'Relevance — low first' },
];

const SORT_VALUES = new Set<string>(SORT_OPTIONS.map((o) => o.value));
export const PAGE_SIZES = [10, 25, 50, 100];

export const FIT_STATUSES: FitStatus[] = [
  'high_fit',
  'good_fit',
  'possible_fit',
  'manual_review',
  'not_fit',
];

export const DEPLOYMENT_FITS: DeploymentFit[] = [
  'cloud_required',
  'cloud_preferred',
  'cloud_allowed',
  'deployment_unspecified',
  'hybrid',
  'mandatory_on_premises',
  'offline_or_air_gapped',
];

/**
 * The default *is* the "Needs attention" view, so a tab is lit on first load and
 * the page opens on the question a bidder actually arrives with. 70 is the
 * engine's good-fit band; the view re-derives it from /api/stats once loaded.
 */
export const DEFAULT_FILTERS: TenderFilters = {
  query: '',
  minimum_score: 70,
  maximum_score: 100,
  sources: [],
  countries: [],
  categories: [],
  statuses: [],
  fit_statuses: [],
  deployment_fits: [],
  deadline_from: '',
  deadline_to: '',
  first_seen_from: '',
  published_from: '',
  published_to: '',
  active_only: true,
  has_deadline: null,
  // Hidden by default, which is the entire point of marking something not
  // relevant: the next person to open the page must not be shown it again.
  hidden: false,
  sort: 'score_desc',
  page: 1,
  page_size: 25,
};

const DATE = /^\d{4}-\d{2}-\d{2}$/;

function clampScore(raw: string | null, fallback: number): number {
  if (raw === null) return fallback;
  const value = Number(raw);
  if (!Number.isFinite(value)) return fallback;
  return Math.min(100, Math.max(0, Math.round(value)));
}

function positiveInt(raw: string | null, fallback: number, max = Number.MAX_SAFE_INTEGER): number {
  if (raw === null) return fallback;
  const value = Number(raw);
  if (!Number.isInteger(value) || value < 1) return fallback;
  return Math.min(value, max);
}

function date(raw: string | null): string {
  return raw && DATE.test(raw) ? raw : '';
}

/** A full ISO instant, kept only if the browser can actually parse it. */
function instant(raw: string | null): string {
  if (!raw) return '';
  return Number.isNaN(new Date(raw).getTime()) ? '' : raw;
}

function bool(raw: string | null, fallback: boolean): boolean {
  if (raw === null) return fallback;
  return raw === 'true' || raw === '1';
}

function tribool(raw: string | null): boolean | null {
  if (raw === 'true' || raw === '1') return true;
  if (raw === 'false' || raw === '0') return false;
  return null;
}

/** Keep only values the backend recognises; a stale link must not 422. */
function subset<T extends string>(values: string[], allowed: readonly T[]): T[] {
  const set = new Set<string>(allowed);
  return values.filter((v): v is T => set.has(v));
}

export function filtersFromSearch(search: string): {
  filters: TenderFilters;
  tenderId: number | null;
} {
  const params = new URLSearchParams(search);
  const all = (key: string) => params.getAll(key).filter(Boolean);
  // The score bounds need care. The default floor is 70 (the Needs-attention
  // view), so a link that supplies only `maximum_score=50` used to be read as
  // "between 50 and 70" — the swap below fired against a default the reader
  // never asked for, inventing both bounds. A lone maximum below the default
  // floor means "cap it here", so the floor drops away instead.
  const rawMin = params.get('minimum_score');
  const rawMax = params.get('maximum_score');
  let minimum = clampScore(rawMin, DEFAULT_FILTERS.minimum_score);
  let maximum = clampScore(rawMax, DEFAULT_FILTERS.maximum_score);
  if (rawMin === null && rawMax !== null && maximum < minimum) minimum = 0;
  if (rawMax === null && rawMin !== null && minimum > maximum) maximum = 100;
  if (rawMin !== null && rawMax !== null && minimum > maximum) {
    // Both supplied but inverted: a sane reading beats returning nothing.
    [minimum, maximum] = [maximum, minimum];
  }
  const sort = params.get('sort');
  const rawTender = params.get('tender');

  return {
    filters: {
      query: params.get('query') ?? '',
      minimum_score: minimum,
      maximum_score: maximum,
      sources: all('sources'),
      countries: all('countries'),
      categories: all('categories'),
      statuses: all('statuses'),
      fit_statuses: subset(all('fit_statuses'), FIT_STATUSES),
      deployment_fits: subset(all('deployment_fits'), DEPLOYMENT_FITS),
      first_seen_from: instant(params.get('first_seen_from')),
      deadline_from: date(params.get('deadline_from')),
      deadline_to: date(params.get('deadline_to')),
      published_from: date(params.get('published_from')),
      published_to: date(params.get('published_to')),
      active_only: bool(params.get('active_only'), DEFAULT_FILTERS.active_only),
      has_deadline: tribool(params.get('has_deadline')),
      // `tribool` reads an absent parameter as null, which is right for
      // has_deadline and wrong here: an absent `hidden` must mean the shipped
      // default (hide them), not "ignore feedback". Only the literal string
      // `all` asks for that, so a link can still say "show me everything".
      hidden: params.get('hidden') === 'all' ? null : bool(params.get('hidden'), false),
      sort: sort && SORT_VALUES.has(sort) ? (sort as SortOption) : DEFAULT_FILTERS.sort,
      page: positiveInt(params.get('page'), 1),
      page_size: PAGE_SIZES.includes(Number(params.get('page_size')))
        ? Number(params.get('page_size'))
        : DEFAULT_FILTERS.page_size,
    },
    tenderId: rawTender && /^\d+$/.test(rawTender) ? Number(rawTender) : null,
  };
}

/** Only non-default values are written, so a shared URL stays readable. */
export function searchFromFilters(filters: TenderFilters, tenderId: number | null): string {
  const params = new URLSearchParams();
  if (filters.query.trim()) params.set('query', filters.query.trim());
  if (filters.minimum_score !== DEFAULT_FILTERS.minimum_score)
    params.set('minimum_score', String(filters.minimum_score));
  if (filters.maximum_score !== DEFAULT_FILTERS.maximum_score)
    params.set('maximum_score', String(filters.maximum_score));
  filters.sources.forEach((v) => params.append('sources', v));
  filters.countries.forEach((v) => params.append('countries', v));
  filters.categories.forEach((v) => params.append('categories', v));
  filters.statuses.forEach((v) => params.append('statuses', v));
  filters.fit_statuses.forEach((v) => params.append('fit_statuses', v));
  filters.deployment_fits.forEach((v) => params.append('deployment_fits', v));
  if (filters.first_seen_from) params.set('first_seen_from', filters.first_seen_from);
  if (filters.deadline_from) params.set('deadline_from', filters.deadline_from);
  if (filters.deadline_to) params.set('deadline_to', filters.deadline_to);
  if (filters.published_from) params.set('published_from', filters.published_from);
  if (filters.published_to) params.set('published_to', filters.published_to);
  if (filters.active_only !== DEFAULT_FILTERS.active_only)
    params.set('active_only', String(filters.active_only));
  if (filters.has_deadline !== null) params.set('has_deadline', String(filters.has_deadline));
  // `all` rather than an omission, because omitting it means the default here.
  if (filters.hidden === null) params.set('hidden', 'all');
  else if (filters.hidden !== DEFAULT_FILTERS.hidden) params.set('hidden', String(filters.hidden));
  if (filters.sort !== DEFAULT_FILTERS.sort) params.set('sort', filters.sort);
  if (filters.page !== 1) params.set('page', String(filters.page));
  if (filters.page_size !== DEFAULT_FILTERS.page_size)
    params.set('page_size', String(filters.page_size));
  if (tenderId !== null) params.set('tender', String(tenderId));
  return params.toString();
}

/** Chip descriptor: a label plus the patch that removes this one filter. */
export interface FilterChip {
  key: string;
  label: string;
  clear: Partial<TenderFilters>;
}

/**
 * What counts as "no constraint at all" - which is not the same as the default
 * view. The default view already narrows 320 stored tenders to a few dozen by
 * applying a score floor and hiding closed notices, so those two have to appear
 * as chips or the result count looks unexplained. Chips answer "why am I seeing
 * this many?", so they are measured against an unfiltered baseline.
 */
const UNCONSTRAINED = {
  minimum_score: 0,
  maximum_score: 100,
  active_only: false,
  // Null, not false: `false` narrows the list (it drops what was rejected), and
  // it narrows it by an amount the reader cannot otherwise account for. It gets
  // a chip for the same reason active_only does, even though both are defaults.
  hidden: null,
} as const;

export function activeChips(
  filters: TenderFilters,
  labels: {
    fit: (v: FitStatus) => string;
    deployment: (v: DeploymentFit) => string;
    source: (v: string) => string;
    category: (v: string) => string;
    country: (v: string) => string;
  },
): FilterChip[] {
  const chips: FilterChip[] = [];
  const list = (values: string[], render: (v: string) => string) => values.map(render).join(' · ');

  if (filters.query.trim())
    chips.push({ key: 'query', label: `“${filters.query.trim()}”`, clear: { query: '' } });
  if (filters.minimum_score !== UNCONSTRAINED.minimum_score)
    chips.push({
      key: 'minimum_score',
      label: `Score ≥ ${filters.minimum_score}`,
      clear: { minimum_score: UNCONSTRAINED.minimum_score },
    });
  if (filters.maximum_score !== UNCONSTRAINED.maximum_score)
    chips.push({
      key: 'maximum_score',
      label: `Score ≤ ${filters.maximum_score}`,
      clear: { maximum_score: UNCONSTRAINED.maximum_score },
    });
  if (filters.fit_statuses.length)
    chips.push({
      key: 'fit_statuses',
      label: list(filters.fit_statuses, (v) => labels.fit(v as FitStatus)),
      clear: { fit_statuses: [] },
    });
  if (filters.deployment_fits.length)
    chips.push({
      key: 'deployment_fits',
      label: list(filters.deployment_fits, (v) => labels.deployment(v as DeploymentFit)),
      clear: { deployment_fits: [] },
    });
  if (filters.categories.length)
    chips.push({
      key: 'categories',
      label: list(filters.categories, labels.category),
      clear: { categories: [] },
    });
  if (filters.sources.length)
    chips.push({
      key: 'sources',
      label: list(filters.sources, labels.source),
      clear: { sources: [] },
    });
  if (filters.countries.length)
    chips.push({
      key: 'countries',
      label: list(filters.countries, (v) => v),
      clear: { countries: [] },
    });
  if (filters.statuses.length)
    chips.push({
      key: 'statuses',
      label: list(filters.statuses, (v) => v),
      clear: { statuses: [] },
    });
  if (filters.first_seen_from)
    chips.push({
      key: 'first_seen_from',
      label: 'Found in the last run',
      clear: { first_seen_from: '' },
    });
  if (filters.deadline_from)
    chips.push({
      key: 'deadline_from',
      label: `Deadline from ${filters.deadline_from}`,
      clear: { deadline_from: '' },
    });
  if (filters.deadline_to)
    chips.push({
      key: 'deadline_to',
      label: `Deadline to ${filters.deadline_to}`,
      clear: { deadline_to: '' },
    });
  if (filters.published_from)
    chips.push({
      key: 'published_from',
      label: `Published from ${filters.published_from}`,
      clear: { published_from: '' },
    });
  if (filters.published_to)
    chips.push({
      key: 'published_to',
      label: `Published to ${filters.published_to}`,
      clear: { published_to: '' },
    });
  if (filters.active_only !== UNCONSTRAINED.active_only)
    chips.push({
      key: 'active_only',
      label: 'Open opportunities only',
      clear: { active_only: UNCONSTRAINED.active_only },
    });
  if (filters.has_deadline !== null)
    chips.push({
      key: 'has_deadline',
      label: filters.has_deadline ? 'Has a deadline' : 'No deadline published',
      clear: { has_deadline: null },
    });
  if (filters.hidden !== UNCONSTRAINED.hidden)
    chips.push({
      key: 'hidden',
      label: filters.hidden ? 'Marked not relevant only' : 'Hiding what was marked not relevant',
      clear: { hidden: UNCONSTRAINED.hidden },
    });
  return chips;
}

/** The number on the "Filters & settings" button. */
export function activeFilterCount(filters: TenderFilters): number {
  return activeChips(filters, {
    fit: (v) => v,
    deployment: (v) => v,
    source: (v) => v,
    category: (v) => v,
    country: (v) => v,
  }).length;
}

/**
 * The page to use when the requested one is past the end of the result set.
 *
 * A shared or stale link can name `page=5` for a six-row result. Left alone that
 * renders zero rows under a count claiming six, with no pager to escape by.
 * Returns null when the requested page is fine, so the caller can skip the
 * state update entirely rather than re-rendering on every load.
 */
export function correctedPage(requested: number, pages: number): number | null {
  if (!Number.isFinite(pages) || pages < 1) return null;
  if (requested <= pages) return null;
  return pages;
}

/**
 * True when nothing is narrowed beyond the shipped default.
 *
 * Compared field by field rather than with JSON.stringify: a parsed filter set
 * has the same values in a different key order, so the string comparison this
 * replaces reported "changed" on a completely fresh load and left the Reset
 * button looking available when there was nothing to reset.
 */
export function isDefaultFilters(filters: TenderFilters): boolean {
  return (Object.keys(DEFAULT_FILTERS) as (keyof TenderFilters)[]).every((key) => {
    const a = filters[key];
    const b = DEFAULT_FILTERS[key];
    if (Array.isArray(a) && Array.isArray(b)) {
      return a.length === b.length && a.every((v, i) => v === b[i]);
    }
    return a === b;
  });
}
