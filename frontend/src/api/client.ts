import type {
  AutomationStatus,
  FetchStartedResponse,
  RescoreResponse,
  ScheduleResponse,
  FetchRun,
  SourceStatus,
  Stats,
  TenderDetail,
  TenderFilters,
  TenderPage,
  TriggerResponse,
  MatchingRules,
  MatchingRulesPatch,
} from '../types';

// Relative by default: Vite proxies in dev, nginx proxies in the Docker image.
const BASE = import.meta.env.VITE_API_BASE_URL ?? '';

export class ApiError extends Error {
  status: number;
  constructor(message: string, status: number) {
    super(message);
    this.status = status;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${BASE}${path}`, {
      headers: { 'Content-Type': 'application/json' },
      ...init,
    });
  } catch {
    throw new ApiError(
      `Cannot reach the API${BASE ? ` at ${BASE}` : ''}. Is the backend running?`,
      0,
    );
  }
  if (!response.ok) {
    let detail = `${response.status} ${response.statusText}`;
    try {
      const body = await response.json();
      if (body?.detail) detail = typeof body.detail === 'string' ? body.detail : detail;
    } catch {
      /* keep the status text */
    }
    throw new ApiError(detail, response.status);
  }
  return (await response.json()) as T;
}

/** Start of a Dhaka day, as the naive-UTC instant the API compares against. */
function dhakaDayStart(day: string): string {
  return new Date(`${day}T00:00:00+06:00`).toISOString().slice(0, 19);
}

/** End of a Dhaka day, inclusive. */
function dhakaDayEnd(day: string): string {
  return new Date(`${day}T23:59:59+06:00`).toISOString().slice(0, 19);
}

/**
 * Filters map one-to-one onto the API's query parameters, which is also what the
 * dashboard puts in its own URL - see src/state/urlFilters.ts.
 */
export function buildQuery(filters: TenderFilters): string {
  const params = new URLSearchParams();
  if (filters.query.trim()) params.set('query', filters.query.trim());
  if (filters.minimum_score > 0) params.set('minimum_score', String(filters.minimum_score));
  if (filters.maximum_score < 100) params.set('maximum_score', String(filters.maximum_score));
  filters.sources.forEach((s) => params.append('sources', s));
  filters.countries.forEach((c) => params.append('countries', c));
  filters.categories.forEach((c) => params.append('categories', c));
  filters.statuses.forEach((s) => params.append('statuses', s));
  filters.fit_statuses.forEach((s) => params.append('fit_statuses', s));
  filters.deployment_fits.forEach((d) => params.append('deployment_fits', d));
  // Date inputs are days, the API takes instants, and every date on screen is
  // rendered in Dhaka. Sending a bare `2026-08-31T00:00:00` had the API read it
  // as UTC, so filtering by the exact date printed on a row could exclude that
  // row by six hours. Convert each day to its Dhaka boundary instead.
  if (filters.deadline_from) params.set('deadline_from', dhakaDayStart(filters.deadline_from));
  if (filters.deadline_to) params.set('deadline_to', dhakaDayEnd(filters.deadline_to));
  if (filters.published_from) params.set('published_from', dhakaDayStart(filters.published_from));
  if (filters.published_to) params.set('published_to', dhakaDayEnd(filters.published_to));
  // Already a full instant, not a day: it comes from the last run's start.
  if (filters.first_seen_from) params.set('first_seen_from', filters.first_seen_from);
  if (filters.active_only) params.set('active_only', 'true');
  if (filters.has_deadline !== null) params.set('has_deadline', String(filters.has_deadline));
  params.set('sort', filters.sort);
  params.set('page', String(filters.page));
  params.set('page_size', String(filters.page_size));
  return params.toString();
}

/**
 * Read-only, with one exception.
 *
 * There is deliberately no startFetch or rescore: fetching is automated and both
 * of those endpoints require the CRON_SECRET header, which a browser must never
 * hold. `setSchedule` and `setTrigger` are the exceptions — *when* the sweep runs,
 * and *whether* it runs at all, are operating decisions a member of staff makes,
 * and the person making one in the dashboard is the authorisation. See
 * docs/DECISIONS.md D19 and D21.
 */
export const api = {
  tenders: (filters: TenderFilters) => request<TenderPage>(`/api/tenders?${buildQuery(filters)}`),
  tender: (id: number) => request<TenderDetail>(`/api/tenders/${id}`),
  sources: () => request<SourceStatus[]>('/api/sources'),
  stats: () => request<Stats>('/api/stats'),
  automation: () => request<AutomationStatus>('/api/automation'),
  fetchRuns: (limit = 20) => request<FetchRun[]>(`/api/fetch-runs?limit=${limit}`),
  setSchedule: (hoursLocal: number[]) =>
    request<ScheduleResponse>('/api/automation/schedule', {
      method: 'PUT',
      body: JSON.stringify({ hours_local: hoursLocal }),
    }),
  setTrigger: (enabled: boolean) =>
    request<TriggerResponse>('/api/automation/trigger', {
      method: 'PUT',
      body: JSON.stringify({ enabled }),
    }),
  /**
   * Start a sweep now. Callable without a secret since D23 — the guards that
   * replaced it live on the server, so a 409 means "already running" and a 429
   * means "too soon", both with a message written for the person who clicked.
   */
  fetchNow: (sources?: string[]) =>
    request<FetchStartedResponse>('/api/fetch', {
      method: 'POST',
      body: JSON.stringify(sources?.length ? { sources } : {}),
    }),
  /** Reload the relevance config and re-score every stored notice. */
  rescore: () => request<RescoreResponse>('/api/tenders/rescore', { method: 'POST' }),

  /**
   * Set or clear a source's API key.
   *
   * Write-only by design: there is no matching read. GET /api/sources reports
   * whether a key is configured and its last four characters, never the value.
   */
  setCredential: (source: string, value: string) =>
    request<void>(`/api/sources/${encodeURIComponent(source)}/credential`, {
      method: 'PUT',
      body: JSON.stringify({ value }),
    }),

  matchingRules: () => request<MatchingRules>('/api/matching-rules'),
  /** Save overrides and re-score. The YAML file itself is never rewritten. */
  saveMatchingRules: (payload: MatchingRulesPatch) =>
    request<RescoreResponse>('/api/matching-rules', {
      method: 'PUT',
      body: JSON.stringify(payload),
    }),
  /** Hand the rules back to the file and re-score. */
  resetMatchingRules: () =>
    request<RescoreResponse>('/api/matching-rules', { method: 'DELETE' }),
};
