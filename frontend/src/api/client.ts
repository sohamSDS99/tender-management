import type {
  AutomationStatus,
  FetchRun,
  SourceStatus,
  Stats,
  TenderDetail,
  TenderFilters,
  TenderPage,
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
  // Date inputs are days; the API takes instants. A "to" date includes its day.
  if (filters.deadline_from) params.set('deadline_from', `${filters.deadline_from}T00:00:00`);
  if (filters.deadline_to) params.set('deadline_to', `${filters.deadline_to}T23:59:59`);
  if (filters.published_from) params.set('published_from', `${filters.published_from}T00:00:00`);
  if (filters.published_to) params.set('published_to', `${filters.published_to}T23:59:59`);
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
 * Read-only. There is deliberately no startFetch or rescore here: fetching is
 * automated (00:00 / 12:00 Asia/Dhaka) and both write endpoints now require the
 * CRON_SECRET header, which a browser must never hold.
 */
export const api = {
  tenders: (filters: TenderFilters) => request<TenderPage>(`/api/tenders?${buildQuery(filters)}`),
  tender: (id: number) => request<TenderDetail>(`/api/tenders/${id}`),
  sources: () => request<SourceStatus[]>('/api/sources'),
  stats: () => request<Stats>('/api/stats'),
  automation: () => request<AutomationStatus>('/api/automation'),
  fetchRuns: (limit = 20) => request<FetchRun[]>(`/api/fetch-runs?limit=${limit}`),
};
