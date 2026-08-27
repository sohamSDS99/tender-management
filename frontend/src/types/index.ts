export type FitStatus = 'high_fit' | 'good_fit' | 'possible_fit' | 'manual_review' | 'not_fit';

export type DeploymentFit =
  | 'cloud_required'
  | 'cloud_preferred'
  | 'cloud_allowed'
  | 'deployment_unspecified'
  | 'hybrid'
  | 'mandatory_on_premises'
  | 'offline_or_air_gapped';

export type SortOption =
  | 'score_desc'
  | 'score_asc'
  | 'deadline_asc'
  | 'deadline_desc'
  | 'published_desc'
  | 'published_asc'
  | 'first_seen_desc';

/** What a reviewer decided about a notice. There is no third value: "nobody
 *  has decided" is the absence of a verdict, not a verdict of its own. */
export type Verdict = 'relevant' | 'irrelevant';

export interface Feedback {
  verdict: Verdict;
  note: string | null;
  created_at: string;
  updated_at: string;
}

export interface Tender {
  id: number;
  source: string;
  source_notice_id: string;
  source_url: string | null;
  reference_number: string | null;
  title: string;
  buyer_name: string | null;
  buyer_country: string | null;
  publication_date: string | null;
  deadline: string | null;
  status: string | null;
  procurement_stage: string | null;
  notice_type: string | null;
  estimated_value: number | null;
  currency: string | null;
  relevance_score: number;
  relevance_category: string | null;
  fit_status: FitStatus;
  deployment_fit: DeploymentFit;
  relevance_reasons: string[];
  disqualifiers: string[];
  review_flags: string[];
  is_actionable: boolean;
  last_seen_at: string;
  first_seen_at: string;
  /** A reviewer's verdict, or null when nobody has made one. */
  feedback: Feedback | null;
  /** The learner's own call: this reads like the notices you rejected. */
  auto_irrelevant: boolean;
  /** The patterns that made it read that way. Never empty when the flag is set. */
  auto_irrelevant_reasons: string[];
  /**
   * Kept out of the working views — by a person, or by the learner.
   *
   * Computed by the API, never re-derived here. It is the same OR the tender
   * filter applies in SQL, and two implementations of it would eventually
   * disagree about a row: the card would show a badge the list had not filtered
   * on, which is the class of bug the score-cap note in CLAUDE.md is about.
   */
  hidden: boolean;
}

export interface TenderDetail extends Tender {
  description: string | null;
  delivery_location: string | null;
  classification_codes: { scheme?: string; code?: string; description?: string | null }[];
  document_urls: string[];
  language: string | null;
  topic_relevance_score: number;
  product_fit_score: number;
  procurement_intent_score: number;
  source_updated_at: string | null;
  source_timezone: string | null;
  content_hash: string;
  created_at: string;
  updated_at: string;
  raw_payload: Record<string, unknown> | null;
}

export interface TenderPage {
  items: Tender[];
  total: number;
  page: number;
  page_size: number;
  pages: number;
}

export interface SourceStatus {
  name: string;
  display_name: string;
  homepage: string;
  enabled: boolean;
  requires_api_key: boolean;
  /** Whether a key is stored. The value itself is never sent to the browser. */
  credential_configured: boolean;
  /** Last four characters, for confirming which key is set. */
  credential_hint: string | null;
  unavailable_reason: string | null;
  keyword_prefiltered: boolean;
  notes: string;
  tender_count: number;
  running: boolean;
  last_status: string | null;
  last_run_at: string | null;
  last_success_at: string | null;
  last_error: string | null;
}

export interface CountBucket {
  key: string;
  label?: string | null;
  count: number;
}

export interface Stats {
  total_tenders: number;
  excellent_fit: number;
  good_fit_or_better: number;
  possible_or_review: number;
  not_relevant: number;
  closing_soon: number;
  actionable: number;
  failed_sources: number;
  last_successful_fetch: string | null;
  by_source: CountBucket[];
  by_fit_status: CountBucket[];
  by_category: CountBucket[];
  by_deployment: CountBucket[];
  countries: string[];
  statuses: string[];
  categories: CountBucket[];
  score_bands: Record<string, number>;
  /**
   * How many notices are hidden. Every other count above *excludes* them, which
   * is what keeps a lens badge equal to the list that lens opens.
   */
  hidden_total: number;
}

export interface FetchRun {
  id: number;
  source: string;
  started_at: string;
  finished_at: string | null;
  status: string;
  records_received: number;
  records_created: number;
  records_updated: number;
  records_skipped: number;
  error_message: string | null;
  window_from: string | null;
  window_to: string | null;
  trigger: string;
  batch_id: string | null;
}

/** Read-only automation picture. There is no way to start a fetch from the UI. */
export interface SlackState {
  status: 'ok' | 'degraded' | 'unconfirmed' | 'disabled' | 'unconfigured';
  detail: string | null;
  sent_total: number;
  unconfirmed: number;
  sent_in_last_batch: number;
  channel_label: string | null;
  min_score: number | null;
  /** Which delivery path is in force. 'none' means Slack cannot send at all. */
  transport: 'bot_token' | 'webhook' | 'none';
}

export interface LastRun {
  batch_id: string | null;
  trigger: string;
  status: string;
  started_at: string;
  finished_at: string | null;
  started_at_local_label: string;
  sources_total: number;
  sources_failed: number;
  records_received: number;
  records_created: number;
  records_updated: number;
  errors: { source: string; message: string }[];
}

export interface AutomationStatus {
  /** The base every Slack digest link is built from. */
  public_app_url: string;
  timezone: string;
  run_hours_local: number[];
  run_hours_are_custom: boolean;
  run_hours_min: number;
  run_hours_max: number;
  /** How deep a dashboard-started sweep looks, in days. The server owns it. */
  operator_fetch_days_back: number;
  cron_utc: string[];
  observes_dst: boolean;
  next_run_at: string;
  next_run_local_label: string;
  /** Whether sweeps are switched on at all: an operator's decision if they made
   *  one, otherwise ENABLE_SCHEDULER. The intent — `scheduler_running` is reality. */
  scheduler_in_process: boolean;
  /** Whether the API process actually has the schedule registered. */
  scheduler_running: boolean;
  scheduler_jobs: { id: string; next_run_at: string | null }[];
  /** True once an operator has set the on/off state, rather than inheriting the env. */
  trigger_is_custom: boolean;
  /** What the environment says, so the reader can see whose decision is in force. */
  trigger_default: boolean;
  /** When the on/off decision was last made. There is no *who*: no accounts (D18). */
  trigger_changed_at: string | null;
  last_run: LastRun | null;
  slack: SlackState;
}

/** The full filter set. Every field round-trips through the URL. */
export interface TenderFilters {
  query: string;
  minimum_score: number;
  maximum_score: number;
  sources: string[];
  countries: string[];
  categories: string[];
  statuses: string[];
  fit_statuses: FitStatus[];
  deployment_fits: DeploymentFit[];
  deadline_from: string;
  deadline_to: string;
  /** ISO instant. Powers the New view: when we first discovered the notice. */
  first_seen_from: string;
  published_from: string;
  published_to: string;
  active_only: boolean;
  has_deadline: boolean | null;
  /**
   * Tri-state, exactly like has_deadline above.
   *
   * `false` (the default) hides what a reviewer rejected and what the learner
   * matched to it; `true` shows only those, which is the review screen an undo
   * needs; `null` ignores feedback entirely. A two-state flag would leave a
   * mistaken mark unreachable.
   */
  hidden: boolean | null;
  sort: SortOption;
  page: number;
  page_size: number;
}

/** Card height. Applied as `html[data-density]`, which the stylesheet keys off. */
export type Density = 'comfortable' | 'compact';

export interface MatchingProfile {
  key: string;
  label: string;
  strong: string[];
  medium: string[];
  weak: string[];
}

export interface MatchingRules {
  weights: Record<string, number>;
  bands: Record<string, number>;
  profiles: MatchingProfile[];
  /** Which sections currently differ from the file. */
  overridden: string[];
  /** File profiles currently switched off — restorable, not gone. */
  removed_profiles: string[];
  /** What the file itself defines, so a removal can be told from a deletion. */
  file_profiles: string[];
}

export type ProfilePatch = Partial<Record<'strong' | 'medium' | 'weak', string[]>> & {
  label?: string;
};

export type MatchingRulesPatch = {
  weights?: Record<string, number>;
  bands?: Record<string, number>;
  profiles?: Record<string, ProfilePatch>;
  removed_profiles?: string[];
};

export interface RulesPreview {
  changed: number;
  crossing_up: number;
  crossing_down: number;
  examined: number;
  total: number;
  /** True when the corpus was sampled, so the UI says "about". */
  sampled: boolean;
  good_fit_band: number;
}

export interface ProbeResult {
  ok: boolean;
  status: number;
  format?: 'ocds' | 'rss' | 'json' | 'unknown';
  records_path?: string;
  found?: number;
  parsed?: number | null;
  paths?: { path: string; sample: string }[];
  reason: string | null;
  detail: string | null;
}

export interface NewSource {
  name: string;
  display_name: string;
  url: string;
  homepage?: string;
  auth: 'none' | 'query' | 'header' | 'bearer';
  auth_param?: string | null;
  format: string;
  mapping?: Record<string, string> | null;
  notes?: string;
  credential?: string;
}

export type SettingsSecrets = Record<string, { configured: boolean; hint: string | null }>;

export interface Preferences {
  density: Density;
  /** Whether the left Settings panel is showing. Persisted so it survives a reload. */
  settingsOpen: boolean;
}

export interface ScheduleResponse {
  hours_local: number[];
  timezone: string;
  cron_utc: string[];
  next_run_local_label: string;
  applied_to_running_scheduler: boolean;
  detail: string;
}

/** What `POST /api/fetch` answers with: 202 and the runs it just created. */
export interface FetchStartedResponse {
  run_ids: number[];
  skipped_sources: string[];
  window_from: string;
  window_to: string;
  /** The window actually searched, in days — echoed back, never assumed. */
  days_back: number;
  /** Groups this sweep's per-source runs, so its progress is attributable. */
  batch_id: string | null;
}

export interface RescoreResponse {
  rescored: number;
}

// --- learning from verdicts (D26) -------------------------------------------

/** One phrase the system worked out for itself, with the evidence for it. */
export interface LearnedPattern {
  phrase: string;
  /** Log-odds: how much more concentrated in rejections than anywhere else. */
  weight: number;
  marked: number;
  elsewhere: number;
}

export interface LearnedModel {
  /** Whether it is allowed to hide anything yet. False until marks_needed is 0. */
  active: boolean;
  marks_irrelevant: number;
  marks_relevant: number;
  /** How many more rejections before the learner starts acting. */
  marks_needed: number;
  corpus: number;
  hidden_total: number;
  hidden_by_learning: number;
  hidden_by_hand: number;
  patterns: LearnedPattern[];
  thresholds: Record<string, number>;
}

export interface FeedbackResponse {
  tender_id: number;
  verdict: Verdict | null;
  /** How many *other* notices this mark just hid or un-hid. */
  reclassified: number;
  learned: LearnedModel;
}

export interface TriggerResponse {
  enabled: boolean;
  is_custom: boolean;
  default: boolean;
  /** Whether the API process actually has a scheduler after the change. */
  scheduler_running: boolean;
  next_run_local_label: string | null;
  detail: string;
}

// --- accounts (D25) ---------------------------------------------------------
//
// A parallel surface, not a field on the data: no tender response gained a
// property when accounts arrived, and a signed-out reader is served exactly what
// they were served before.

export type UserRole = 'admin' | 'member';

/** A person, as the API is willing to describe them. Never carries a secret. */
export interface User {
  id: number;
  email: string;
  display_name: string;
  role: UserRole;
  is_active: boolean;
  created_at: string;
  last_login_at: string | null;
}

/**
 * The answer to one call at page load.
 *
 * `user: null` is the ordinary signed-out state and not an error — the whole
 * dashboard works without an account.
 */
export interface SessionState {
  user: User | null;
  /** No account exists yet: the next registration takes the admin slot. */
  bootstrap: boolean;
  /** The mirror of `bootstrap`, named for what the registration form asks. */
  invite_required: boolean;
}

/** One signed-in browser, in the profile's session list. */
export interface AuthSession {
  id: number;
  created_at: string;
  last_seen_at: string;
  expires_at: string;
  user_agent: string;
  /** The browser reading this list. Marked so nobody ends their own by mistake. */
  current: boolean;
}

export type InviteStatus = 'pending' | 'accepted' | 'expired' | 'revoked';

export interface Invite {
  id: number;
  email: string | null;
  role: UserRole;
  note: string;
  status: InviteStatus;
  created_at: string;
  expires_at: string;
  accepted_at: string | null;
}

/**
 * The one response in the API carrying a live credential.
 *
 * `token` and `url` are returned once, at creation, because only a hash of the
 * token is stored. There is no endpoint that can show them again.
 */
export interface InviteCreated {
  invite: Invite;
  token: string;
  url: string;
}

export interface RevokedCount {
  revoked: number;
}
