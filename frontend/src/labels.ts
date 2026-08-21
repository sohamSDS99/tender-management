import type { DeploymentFit, FitStatus } from './types';

/**
 * Presentation helpers.
 *
 * The deadline urgency thresholds and the currency rendering mirror
 * backend/app/services/notifier.py deliberately: a tender must not look
 * "8 days left" in Slack and "soon" with a different colour in the dashboard.
 * If one side changes, change both.
 */

export const DHAKA = 'Asia/Dhaka';

export const FIT_LABELS: Record<FitStatus, string> = {
  high_fit: 'Excellent fit',
  good_fit: 'Good fit',
  possible_fit: 'Possible fit',
  manual_review: 'Manual review',
  not_fit: 'Not fit',
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

/** Green / amber / red, matching the score pill and the fit badge. */
export function fitTone(fit: FitStatus): 'green' | 'amber' | 'red' {
  if (fit === 'high_fit' || fit === 'good_fit') return 'green';
  if (fit === 'possible_fit' || fit === 'manual_review') return 'amber';
  return 'red';
}

export function deploymentTone(value: DeploymentFit): 'green' | 'amber' | 'red' | 'grey' {
  if (value === 'cloud_required' || value === 'cloud_preferred' || value === 'cloud_allowed')
    return 'green';
  if (value === 'hybrid') return 'amber';
  if (value === 'mandatory_on_premises' || value === 'offline_or_air_gapped') return 'red';
  return 'grey';
}

export function fitLabel(value: string): string {
  return FIT_LABELS[value as FitStatus] ?? value;
}

export function deploymentLabel(value: string): string {
  return DEPLOYMENT_LABELS[value as DeploymentFit] ?? value;
}

export function scoreTone(score: number): 'green' | 'amber' | 'red' {
  if (score >= 70) return 'green';
  if (score >= 40) return 'amber';
  return 'red';
}

/** Dates are naive UTC from the API; readers think in Dhaka time. */
export function formatDate(value: string | null | undefined): string {
  if (!value) return '—';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '—';
  return date.toLocaleDateString('en-GB', {
    day: '2-digit',
    month: 'short',
    year: 'numeric',
    timeZone: DHAKA,
  });
}

export function formatDateTime(value: string | null | undefined): string {
  if (!value) return '—';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '—';
  return date.toLocaleString('en-GB', {
    day: '2-digit',
    month: 'short',
    year: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
    timeZone: DHAKA,
  });
}

export function formatTime(value: string | null | undefined): string {
  if (!value) return '—';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '—';
  return date.toLocaleTimeString('en-GB', {
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
    timeZone: DHAKA,
  });
}

export type Urgency = 'urgent' | 'soon' | 'normal' | 'gone' | 'none';

/** Colour-coded at 14 days and 72 hours - the same bands the digest uses. */
export function deadlineUrgency(
  deadline: string | null,
  now: Date = new Date(),
): { urgency: Urgency; label: string } {
  if (!deadline) return { urgency: 'none', label: 'no deadline in feed' };
  const at = new Date(deadline);
  if (Number.isNaN(at.getTime())) return { urgency: 'none', label: 'no deadline in feed' };
  const ms = at.getTime() - now.getTime();
  if (ms <= 0) return { urgency: 'gone', label: 'closed' };
  const hours = ms / 3_600_000;
  if (hours <= 72) {
    const whole = Math.floor(hours);
    return { urgency: 'urgent', label: whole >= 1 ? `${whole}h left` : 'closes within the hour' };
  }
  const days = Math.floor(hours / 24);
  if (days <= 14) return { urgency: 'soon', label: `${days} day${days === 1 ? '' : 's'} left` };
  return { urgency: 'normal', label: `${days} days left` };
}

const CURRENCY_SYMBOLS: Record<string, string> = {
  EUR: '€',
  USD: '$',
  GBP: '£',
  CAD: 'CA$',
  AUD: 'A$',
  BRL: 'R$',
};

export function formatValue(amount: number | null, currency: string | null): string {
  if (amount === null || amount === undefined) return 'value not published';
  const rendered = Math.round(amount).toLocaleString('en-GB');
  const symbol = CURRENCY_SYMBOLS[(currency ?? '').toUpperCase()];
  if (symbol) return `${symbol}${rendered}`;
  return currency ? `${rendered} ${currency}` : rendered;
}

/** 'success' | 'partial' | 'failed' | 'skipped' -> a health colour. */
export function runTone(status: string | null): 'good' | 'warning' | 'critical' | 'idle' {
  if (status === 'success') return 'good';
  if (status === 'partial') return 'warning';
  if (status === 'failed') return 'critical';
  return 'idle';
}

export function pluralise(count: number, singular: string, plural?: string): string {
  return count === 1 ? singular : (plural ?? `${singular}s`);
}
