import type { DeploymentFit, FitStatus } from './types';

export const FIT_LABELS: Record<FitStatus, string> = {
  high_fit: 'Excellent fit',
  good_fit: 'Good fit',
  possible_fit: 'Possible fit',
  manual_review: 'Manual review',
  not_fit: 'Not fit',
};

/** green = cloud-compatible, amber = needs a human, red = disqualifying. */
export const FIT_TONE: Record<FitStatus, 'green' | 'amber' | 'red'> = {
  high_fit: 'green',
  good_fit: 'green',
  possible_fit: 'amber',
  manual_review: 'amber',
  not_fit: 'red',
};

export const DEPLOYMENT_LABELS: Record<DeploymentFit, string> = {
  cloud_required: 'Cloud required',
  cloud_preferred: 'Cloud preferred',
  cloud_allowed: 'Cloud allowed',
  deployment_unspecified: 'Deployment unspecified',
  hybrid: 'Hybrid (cloud or on-prem)',
  mandatory_on_premises: 'Mandatory on-premises',
  offline_or_air_gapped: 'Offline / air-gapped',
};

export const DEPLOYMENT_TONE: Record<DeploymentFit, 'green' | 'amber' | 'red' | 'grey'> = {
  cloud_required: 'green',
  cloud_preferred: 'green',
  cloud_allowed: 'green',
  deployment_unspecified: 'grey',
  hybrid: 'amber',
  mandatory_on_premises: 'red',
  offline_or_air_gapped: 'red',
};

export const CATEGORY_LABELS: Record<string, string> = {
  sds_management: 'SDS management',
  sds_authoring: 'SDS authoring',
  sds_distribution: 'SDS distribution',
  chemical_compliance: 'Chemical & GHS compliance',
  ehs_platform: 'EHS platform',
  incident_management: 'Incident management',
  inspection_management: 'Inspection management',
  audit_management: 'Audit management',
};

export const SORT_OPTIONS: { value: string; label: string }[] = [
  { value: 'score_desc', label: 'Relevance (high to low)' },
  { value: 'deadline_asc', label: 'Deadline (soonest first)' },
  { value: 'published_desc', label: 'Published (newest first)' },
  { value: 'first_seen_desc', label: 'Recently discovered' },
  { value: 'score_asc', label: 'Relevance (low to high)' },
];

export function scoreTone(score: number): 'green' | 'amber' | 'red' {
  if (score >= 70) return 'green';
  if (score >= 50) return 'amber';
  return 'red';
}

export function formatDate(value: string | null | undefined): string {
  if (!value) return '—';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '—';
  return date.toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: '2-digit' });
}

export function formatDateTime(value: string | null | undefined): string {
  if (!value) return '—';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '—';
  return date.toLocaleString(undefined, {
    year: 'numeric',
    month: 'short',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  });
}

export function formatValue(amount: number | null, currency: string | null): string {
  if (amount === null || amount === undefined) return '—';
  try {
    return new Intl.NumberFormat(undefined, {
      style: currency ? 'currency' : 'decimal',
      currency: currency ?? undefined,
      maximumFractionDigits: 0,
    }).format(amount);
  } catch {
    return `${amount.toLocaleString()} ${currency ?? ''}`.trim();
  }
}

export function daysUntil(value: string | null): number | null {
  if (!value) return null;
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return null;
  return Math.ceil((date.getTime() - Date.now()) / 86_400_000);
}
