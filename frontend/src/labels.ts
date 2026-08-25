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

/**
 * Score bands come from the engine, not from guessed numbers.
 *
 * Hardcoding 70/40 put a score of 40 in an amber pill next to a red "Not fit"
 * badge — two different verdicts on the same notice in the same row. The engine's
 * own possible-fit band is 50, so the bands are passed in from /api/stats.
 */
export interface ScoreBands {
  good_fit: number;
  possible_fit: number;
}

export const FALLBACK_BANDS: ScoreBands = { good_fit: 70, possible_fit: 50 };

export function scoreTone(
  score: number,
  bands: ScoreBands = FALLBACK_BANDS,
): 'green' | 'amber' | 'red' {
  if (score >= bands.good_fit) return 'green';
  if (score >= bands.possible_fit) return 'amber';
  return 'red';
}

/** Turn a machine source key into the display name /api/sources already gives. */
export function makeSourceLabel(names: Record<string, string>) {
  return (key: string | null | undefined): string => {
    if (!key) return '—';
    return names[key] ?? key.replace(/_/g, ' ');
  };
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

/**
 * Is a sweep still going, judged from a batch status?
 *
 * `queued` matters as much as `running` and is the easier one to forget. Every
 * FetchRun row is created `queued` and only becomes `running` when its connector
 * starts, so a batch reads `queued` for the first instant of its life. Testing
 * only for `running` leaves a window where the page decides the sweep is over
 * before it has begun — it would stop polling, and the progress it just promised
 * to keep up to date would freeze until something else happened to reload.
 */
export function isSweepInFlight(status: string | null | undefined): boolean {
  return status === 'running' || status === 'queued';
}

export type SourceHealth = 'good' | 'warning' | 'critical' | 'idle' | 'sweeping';

/**
 * How a source is doing, with "busy" told apart from "broken".
 *
 * This exists because the summary line read **"0 of 8 sources healthy"** during a
 * sweep. Starting a sweep sets every source to `queued`, `runTone` maps anything
 * it does not recognise to `idle`, and the healthy count only counts `good` — so
 * the page announced total connector failure at the exact moment the system was
 * working. That is the most alarming string on the dashboard, and it was false.
 *
 * `sweeping` is its own state rather than a flavour of healthy: mid-sweep we
 * genuinely do not know yet how the source will do, and claiming either answer
 * would be a guess.
 */
export function sourceHealth(source: {
  enabled: boolean;
  unavailable_reason: string | null;
  running: boolean;
  last_status: string | null;
}): SourceHealth {
  // A source that cannot run is not busy, whatever its last run said.
  if (source.unavailable_reason) return 'critical';
  if (!source.enabled) return 'idle';
  // The live flag leads the run row: /api/sources reads it straight from the
  // in-process set, so it is true before the FetchRun row has been updated.
  if (source.running || source.last_status === 'running' || source.last_status === 'queued')
    return 'sweeping';
  return runTone(source.last_status);
}

/**
 * What a sweep did, in a sentence a bidder can act on.
 *
 * The page used to say "Sweep started across 7 sources" and then nothing, ever.
 * So a sweep that stored eight notices and a sweep that stored none looked
 * identical — which is precisely what "it is not coming up with any new tender"
 * turned out to mean. Every branch below is a different, sayable outcome, and
 * "seen but already stored" is deliberately not collapsed into "found nothing":
 * they call for completely different actions.
 */
export function sweepSummary(run: {
  created: number;
  updated: number;
  received: number;
  daysBack: number;
  done: boolean;
}): string {
  const n = (value: number) => value.toLocaleString('en-GB');
  const days = `${run.daysBack} ${pluralise(run.daysBack, 'day')}`;

  if (!run.done) {
    return `Sweeping the last ${days}… ${n(run.received)} seen so far, ${n(run.created)} new.`;
  }
  if (run.received === 0) {
    return `No notices at all were returned for the last ${days}. Check source health below.`;
  }
  if (run.created === 0) {
    return `No new notices. ${n(run.received)} seen across the last ${days} were already stored, ${n(run.updated)} updated.`;
  }
  return `${n(run.created)} new, ${n(run.updated)} updated from ${n(run.received)} ${pluralise(run.received, 'notice')} seen across the last ${days}.`;
}

/**
 * Fallback sweep depth, in days, used only until /api/automation answers.
 *
 * The server owns this value (OPERATOR_FETCH_DAYS_BACK) for the same reason it
 * owns the score bands: two copies of a number drift, and this one decides
 * whether the Fetch button searches a window the scheduler has already emptied.
 */
export const FALLBACK_SWEEP_DAYS = 30;

/** The depths offered at the point of action. Bounded by what the API accepts. */
export const SWEEP_DEPTHS = [3, 7, 30, 90] as const;

export function pluralise(count: number, singular: string, plural?: string): string {
  return count === 1 ? singular : (plural ?? `${singular}s`);
}

/**
 * Only allow a feed-supplied URL to become a link if its scheme is http(s).
 *
 * `source_url` and `document_urls` come from eight external feeds, and React
 * does not block `javascript:` in an href — it renders it and the browser will
 * execute it on click. Anything that is not plainly http(s) is rendered as
 * inert text instead of a link.
 */
export function safeHref(url: string | null | undefined): string | null {
  if (!url) return null;
  const trimmed = url.trim();
  // An explicit http(s) scheme is required rather than resolved against our own
  // origin: a blank or relative value from a feed is meaningless as a notice
  // link, and resolving it would silently produce a link back into this app.
  if (!/^https?:\/\//i.test(trimmed)) return null;
  try {
    return new URL(trimmed).href;
  } catch {
    return null;
  }
}

/** Filename-ish label for a document URL, falling back to the URL itself. */
export function linkLabel(url: string): string {
  try {
    const path = new URL(url, window.location.origin).pathname;
    const last = path.split('/').filter(Boolean).pop();
    return last ? decodeURIComponent(last) : url;
  } catch {
    return url;
  }
}

/* ---------------------------------------------------------------------------
 * Country names
 *
 * The eight feeds disagree about how to write a country: TED sends ISO alpha-3
 * ("DEU"), the OCDS feeds send alpha-2 ("GB"), and the World Bank sends the name
 * outright ("Indonesia"). Left alone, the filter list reads
 * "AU · Bangladesh · DEU · Eastern and Southern Africa", which is noise to
 * someone who just wants to tick "Germany".
 *
 * Intl.DisplayNames does the alpha-2 case for free and needs no dependency, but
 * it does not accept alpha-3 - hence the map, which covers the codes the feeds
 * actually emit (TED is EU-wide, plus the non-EU countries seen in the data).
 * Anything unrecognised is passed through untouched rather than guessed at.
 * --------------------------------------------------------------------------- */
const ALPHA3_TO_ALPHA2: Record<string, string> = {
  AUS: 'AU',
  AUT: 'AT',
  BEL: 'BE',
  BGR: 'BG',
  BRA: 'BR',
  CAN: 'CA',
  CHE: 'CH',
  CYP: 'CY',
  CZE: 'CZ',
  DEU: 'DE',
  DNK: 'DK',
  ESP: 'ES',
  EST: 'EE',
  FIN: 'FI',
  FRA: 'FR',
  GBR: 'GB',
  GRC: 'GR',
  HRV: 'HR',
  HUN: 'HU',
  IRL: 'IE',
  ISL: 'IS',
  ITA: 'IT',
  LIE: 'LI',
  LTU: 'LT',
  LUX: 'LU',
  LVA: 'LV',
  MLT: 'MT',
  NLD: 'NL',
  NOR: 'NO',
  POL: 'PL',
  PRT: 'PT',
  ROU: 'RO',
  SVK: 'SK',
  SVN: 'SI',
  SWE: 'SE',
  TUR: 'TR',
  USA: 'US',
};

let regionNames: Intl.DisplayNames | null | undefined;

function regions(): Intl.DisplayNames | null {
  if (regionNames === undefined) {
    try {
      regionNames = new Intl.DisplayNames(['en'], { type: 'region' });
    } catch {
      regionNames = null;
    }
  }
  return regionNames;
}

/** "DEU" and "DE" both become "Germany"; "Indonesia" stays as it is. */
export function countryLabel(value: string | null | undefined): string {
  if (!value) return '—';
  const raw = value.trim();
  if (raw.length > 3) return raw;

  const code = raw.toUpperCase();
  const alpha2 = code.length === 3 ? ALPHA3_TO_ALPHA2[code] : code;
  if (!alpha2 || alpha2.length !== 2) return raw;
  try {
    return regions()?.of(alpha2) ?? raw;
  } catch {
    return raw;
  }
}

/* ---------------------------------------------------------------------------
 * Relative time
 *
 * "3 hours ago" is instantly parseable; "21 Aug 2026, 16:59" makes the reader do
 * arithmetic. Used for the sweep status, where the question is always "is this
 * fresh?" rather than "what was the timestamp?".
 * --------------------------------------------------------------------------- */
export function relativeTime(value: string | null | undefined, now: Date = new Date()): string {
  if (!value) return 'never';
  const at = new Date(value);
  if (Number.isNaN(at.getTime())) return 'never';

  const seconds = Math.round((at.getTime() - now.getTime()) / 1000);
  const past = seconds < 0;
  const abs = Math.abs(seconds);

  const steps: [number, Intl.RelativeTimeFormatUnit][] = [
    [60, 'second'],
    [3600, 'minute'],
    [86_400, 'hour'],
    [604_800, 'day'],
  ];
  if (abs < 45) return past ? 'just now' : 'in a moment';

  let unit: Intl.RelativeTimeFormatUnit = 'day';
  let amount = Math.round(abs / 86_400);
  for (const [limit, candidate] of steps) {
    if (abs < limit) {
      unit = candidate;
      const divisor =
        candidate === 'second'
          ? 1
          : candidate === 'minute'
            ? 60
            : candidate === 'hour'
              ? 3600
              : 86_400;
      amount = Math.round(abs / divisor);
      break;
    }
  }
  try {
    return new Intl.RelativeTimeFormat('en', { numeric: 'auto' }).format(
      past ? -amount : amount,
      unit,
    );
  } catch {
    return past ? `${amount} ${unit}s ago` : `in ${amount} ${unit}s`;
  }
}

/**
 * A time, or a date and time once it is no longer today.
 *
 * `formatTime` alone renders a run from four days ago as "14:05", which reads
 * as this afternoon. That is how a source skipped last Friday looked like a
 * source failing right now.
 */
export function formatWhen(value: string | null | undefined): string {
  if (!value) return '—';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '—';
  const day = (d: Date) => d.toLocaleDateString('en-CA', { timeZone: DHAKA });
  if (day(date) === day(new Date())) return formatTime(value);
  return date.toLocaleString('en-GB', {
    day: 'numeric',
    month: 'short',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
    timeZone: DHAKA,
  });
}
