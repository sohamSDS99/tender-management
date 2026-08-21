export type FitStatus = 'high_fit' | 'good_fit' | 'possible_fit' | 'manual_review' | 'not_fit';

export type DeploymentFit =
  | 'cloud_required'
  | 'cloud_preferred'
  | 'cloud_allowed'
  | 'deployment_unspecified'
  | 'hybrid'
  | 'mandatory_on_premises'
  | 'offline_or_air_gapped';

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
  first_seen_at: string;
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
}

export interface FetchResponse {
  runs: { id: number; source: string; status: string }[];
  run_ids: number[];
  skipped_sources: string[];
  window_from: string;
  window_to: string;
}

export interface TenderFilters {
  query: string;
  minimum_score: number;
  sources: string[];
  countries: string[];
  categories: string[];
  statuses: string[];
  fit_statuses: FitStatus[];
  deployment_fits: DeploymentFit[];
  deadline_to: string;
  active_only: boolean;
  sort: string;
  page: number;
  page_size: number;
}
