import { FALLBACK_SWEEP_DAYS } from '../labels';
import type {
  AutomationStatus,
  FeedbackResponse,
  FetchStartedResponse,
  LearnedModel,
  RescoreResponse,
  Verdict,
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
  NewSource,
  ProbeResult,
  RulesPreview,
  SettingsSecrets,
  AuthSession,
  Invite,
  InviteCreated,
  RevokedCount,
  SessionState,
  User,
  UserRole,
  RosterAdded,
  RosterEntry,
  RosterView,
} from '../types';

// Relative by default: Vite proxies in dev, nginx proxies in the Docker image.
const BASE = import.meta.env.VITE_API_BASE_URL ?? '';

/**
 * Called when the API refuses a request from someone who believed they were
 * signed in — an expired session, a revoked one, a deactivated account.
 *
 * Registered by `useAuth`. Without it a session that dies mid-visit leaves the
 * dashboard mounted and every panel showing its own error, which reads as the
 * backend being broken rather than as "you are signed out".
 */
let onUnauthorized: (() => void) | null = null;

export function setUnauthorizedHandler(handler: (() => void) | null): void {
  onUnauthorized = handler;
}

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
      // The session cookie (D25). `same-origin` is fetch's default and covers
      // both supported deployments, but VITE_API_BASE_URL can point the page at
      // another origin, and there the default silently sends no cookie — the
      // symptom is a sign-in that returns 200 and leaves you signed out. The
      // API only allows credentials for origins named in CORS_ORIGINS.
      credentials: 'include',
      ...init,
    });
  } catch {
    throw new ApiError(
      `Cannot reach the API${BASE ? ` at ${BASE}` : ''}. Is the backend running?`,
      0,
    );
  }
  if (!response.ok) {
    // Deliberately not for /api/auth/*: a wrong password there is a 401 that the
    // sign-in form is already reporting, and treating it as "your session died"
    // would fight with the page the reader is looking at.
    if (response.status === 401 && !path.startsWith('/api/auth/')) onUnauthorized?.();
    let detail = `${response.status} ${response.statusText}`;
    try {
      const body = await response.json();
      if (body?.detail) detail = typeof body.detail === 'string' ? body.detail : detail;
    } catch {
      /* keep the status text */
    }
    throw new ApiError(detail, response.status);
  }
  // 204 carries no body, so parsing it throws "Unexpected end of JSON input" —
  // which surfaced as a failure on a PUT that had in fact succeeded. Checked
  // here rather than at the call site so every no-content endpoint is covered.
  if (response.status === 204 || response.headers.get('content-length') === '0') {
    return undefined as T;
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
  // Always sent when it is not null, including `false` — `false` is the whole
  // point of the parameter (hide what was rejected), so the usual
  // "omit the default" shortening would silently turn the default view off.
  if (filters.hidden !== null) params.set('hidden', String(filters.hidden));
  params.set('sort', filters.sort);
  params.set('page', String(filters.page));
  params.set('page_size', String(filters.page_size));
  return params.toString();
}

/**
 * Reads, plus the four operating decisions a member of staff is allowed to make.
 *
 * Nothing here holds a secret. `setSchedule` and `setTrigger` decide *when* and
 * *whether* the sweep runs (D19, D21); `fetchNow` and `rescore` are the two
 * expensive actions, callable since D23 because the shared secret was replaced by
 * server-side cost controls rather than put in the page.
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
   * Start a sweep now, over an explicit window.
   *
   * Callable without a secret since D23 — the guards that replaced it live on the
   * server, so a 409 means "already running" and a 429 means "too soon", both
   * with a message written for the person who clicked.
   *
   * `daysBack` is the whole point of this call. Sending an empty body used to let
   * the backend fall back to the scheduler's 72-hour overlap, which by the time a
   * human presses the button holds nothing it has not already stored — so the
   * sweep truthfully reported success and created nothing. The depth is always
   * sent, and it is always what the operator chose.
   */
  fetchNow: (options: { sources?: string[]; daysBack?: number } = {}) =>
    request<FetchStartedResponse>('/api/fetch', {
      method: 'POST',
      body: JSON.stringify({
        days_back: options.daysBack ?? FALLBACK_SWEEP_DAYS,
        // Omitted rather than sent empty: an empty list is a different request
        // from "every enabled source", and only one of them is what we mean.
        ...(options.sources?.length ? { sources: options.sources } : {}),
      }),
    }),
  /** Reload the relevance config and re-score every stored notice. */
  rescore: () => request<RescoreResponse>('/api/tenders/rescore', { method: 'POST' }),

  /**
   * Mark one notice relevant or not relevant (D27).
   *
   * Uncapped, unlike `fetchNow` and `rescore`: it spends no outbound request,
   * and marks are made in bursts of a dozen while someone reads a page. The
   * response says how many *other* notices the mark reclassified, which is the
   * only way the learning is visible at the moment it happens.
   */
  setFeedback: (id: number, verdict: Verdict, note?: string) =>
    request<FeedbackResponse>(`/api/tenders/${id}/feedback`, {
      method: 'POST',
      body: JSON.stringify({ verdict, note: note ?? null }),
    }),
  /** Withdraw a verdict. 200 even when there was nothing to withdraw. */
  clearFeedback: (id: number) =>
    request<FeedbackResponse>(`/api/tenders/${id}/feedback`, { method: 'DELETE' }),
  /** The patterns learned so far, with the evidence for each. */
  learned: () => request<LearnedModel>('/api/feedback/learned'),

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

  /** Try a candidate endpoint. Stores nothing; reports what parsed. */
  probeSource: (body: {
    url: string;
    auth?: string;
    auth_param?: string | null;
    credential?: string;
    mapping?: Record<string, string> | null;
  }) => request<ProbeResult>('/api/sources/probe', { method: 'POST', body: JSON.stringify(body) }),
  addSource: (body: NewSource) =>
    request<{ name: string }>('/api/sources', { method: 'POST', body: JSON.stringify(body) }),
  deleteSource: (name: string) =>
    request<void>(`/api/sources/${encodeURIComponent(name)}`, { method: 'DELETE' }),

  settingsSecrets: () => request<SettingsSecrets>('/api/settings/secrets'),
  /** Set or clear one operator-settable value. Write-only for the secret ones. */
  setSettingsSecret: (field: string, value: string) =>
    request<void>(`/api/settings/secrets/${encodeURIComponent(field)}`, {
      method: 'PUT',
      body: JSON.stringify({ value }),
    }),

  matchingRules: () => request<MatchingRules>('/api/matching-rules'),
  /** What a rule change would move, without moving it. Stores nothing. */
  previewMatchingRules: (payload: MatchingRulesPatch) =>
    request<RulesPreview>('/api/matching-rules/preview', {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  /** Save overrides and re-score. The YAML file itself is never rewritten. */
  saveMatchingRules: (payload: MatchingRulesPatch) =>
    request<RescoreResponse>('/api/matching-rules', {
      method: 'PUT',
      body: JSON.stringify(payload),
    }),
  /** Hand the rules back to the file and re-score. */
  resetMatchingRules: () => request<RescoreResponse>('/api/matching-rules', { method: 'DELETE' }),
};

/**
 * Accounts (D25). Grouped separately from `api` because it is a different kind
 * of surface: everything above reads or operates on procurement notices and
 * answers anybody, while roughly half of this refuses a caller.
 *
 * No token is handled here. The session is an HttpOnly cookie the browser
 * attaches on its own, which is precisely why script cannot read it — so there
 * is nothing for this module to store, refresh or accidentally log.
 */
export const auth = {
  /**
   * Who is signed in, if anyone. Called once at page load.
   *
   * A 200 with `user: null` is the ordinary signed-out answer, so this never
   * needs a 401 branch — see the note on the endpoint itself.
   */
  session: () => request<SessionState>('/api/auth/session'),
  register: (body: {
    email: string;
    password: string;
    display_name: string;
    /** A single-use invitation (D25), for somebody not on the roster. */
    invite_token?: string | null;
    /** The shared workspace link (D28). Only works if the address is listed. */
    join_token?: string | null;
  }) => request<User>('/api/auth/register', { method: 'POST', body: JSON.stringify(body) }),
  login: (email: string, password: string) =>
    request<User>('/api/auth/login', {
      method: 'POST',
      body: JSON.stringify({ email, password }),
    }),
  logout: () => request<void>('/api/auth/logout', { method: 'POST' }),
  /**
   * Open an access link: creates the account if needed and signs in (D29).
   *
   * A POST rather than following the URL, so a chat client building a link
   * preview cannot consume the invitation before the person sees it.
   */
  accept: (token: string) =>
    request<User>('/api/auth/accept', { method: 'POST', body: JSON.stringify({ token }) }),

  updateProfile: (body: { display_name?: string; email?: string }) =>
    request<User>('/api/auth/me', { method: 'PATCH', body: JSON.stringify(body) }),
  /** Returns how many *other* sessions the change ended. This one survives. */
  changePassword: (currentPassword: string, newPassword: string) =>
    request<RevokedCount>('/api/auth/me/password', {
      method: 'POST',
      body: JSON.stringify({ current_password: currentPassword, new_password: newPassword }),
    }),
  sessions: () => request<AuthSession[]>('/api/auth/sessions'),
  /** Ends every session but this one, so success is not a sign-in screen. */
  signOutOthers: () => request<RevokedCount>('/api/auth/sessions', { method: 'DELETE' }),

  invites: () => request<Invite[]>('/api/auth/invites'),
  /** The response carries the only copy of the link. Show it, do not discard it. */
  createInvite: (body: { email?: string | null; role: UserRole; note?: string }) =>
    request<InviteCreated>('/api/auth/invites', { method: 'POST', body: JSON.stringify(body) }),
  revokeInvite: (id: number) => request<void>(`/api/auth/invites/${id}`, { method: 'DELETE' }),

  /** The roster and the join link in one call — the panel needs both. */
  roster: () => request<RosterView>('/api/auth/roster'),
  /** A pasted blob: commas, semicolons, spaces and newlines all work. */
  addToRoster: (body: { addresses: string; role: UserRole; note?: string }) =>
    request<RosterAdded>('/api/auth/roster', { method: 'POST', body: JSON.stringify(body) }),
  /** Changes what a *future* account gets. Does not move an existing one. */
  setRosterRole: (id: number, role: UserRole) =>
    request<RosterEntry>(`/api/auth/roster/${id}`, {
      method: 'PATCH',
      body: JSON.stringify({ role }),
    }),
  /** Withdraws permission to register. Does not close an existing account. */
  removeFromRoster: (id: number) => request<void>(`/api/auth/roster/${id}`, { method: 'DELETE' }),
  /** Mint this person's link, replacing any previous one. */
  issueAccessLink: (id: number) =>
    request<RosterEntry>(`/api/auth/roster/${id}/link`, { method: 'POST' }),
  /** Withdraw their link. Does not end a session they already hold. */
  revokeAccessLink: (id: number) =>
    request<void>(`/api/auth/roster/${id}/link`, { method: 'DELETE' }),

  users: () => request<User[]>('/api/auth/users'),
  updateUser: (id: number, body: { role?: UserRole; is_active?: boolean }) =>
    request<User>(`/api/auth/users/${id}`, { method: 'PATCH', body: JSON.stringify(body) }),
};
