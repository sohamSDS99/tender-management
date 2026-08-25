import { describe, expect, it } from 'vitest';
import {
  countryLabel,
  deadlineUrgency,
  deploymentLabel,
  fitLabel,
  formatValue,
  relativeTime,
  safeHref,
  isSweepInFlight,
  scoreTone,
  sourceHealth,
  sweepSummary,
} from './labels';

/**
 * The urgency bands and the currency rendering mirror
 * backend/app/services/notifier.py on purpose. A tender must not read
 * "8 days left" in Slack and something else in the dashboard, so these tests
 * encode the same boundaries the Python tests assert.
 */

const NOW = new Date('2026-08-21T06:00:00Z');

describe('deadline urgency matches the digest bands', () => {
  const at = (ms: number) => new Date(NOW.getTime() + ms).toISOString();
  const hours = (h: number) => at(h * 3_600_000);
  const days = (d: number) => at(d * 86_400_000);

  it('colour-codes at 72 hours and 14 days', () => {
    expect(deadlineUrgency(hours(71), NOW).urgency).toBe('urgent');
    expect(deadlineUrgency(hours(73), NOW).urgency).toBe('soon');
    expect(deadlineUrgency(days(14), NOW).urgency).toBe('soon');
    expect(deadlineUrgency(days(15), NOW).urgency).toBe('normal');
  });

  it('reports a passed deadline as closed', () => {
    expect(deadlineUrgency(hours(-1), NOW)).toEqual({ urgency: 'gone', label: 'closed' });
  });

  it('handles a missing or unparseable deadline', () => {
    expect(deadlineUrgency(null, NOW).urgency).toBe('none');
    expect(deadlineUrgency('not-a-date', NOW).urgency).toBe('none');
  });

  it('counts down in hours inside the urgent band and days outside it', () => {
    // Anything past 72 hours is at least 3 days, so the "soon" band never
    // reports a single day - inside 72 hours it counts hours instead.
    expect(deadlineUrgency(hours(24), NOW).label).toBe('24h left');
    expect(deadlineUrgency(days(8), NOW).label).toBe('8 days left');
    expect(deadlineUrgency(hours(0.5), NOW).label).toBe('closes within the hour');
  });
});

describe('value formatting', () => {
  it('uses a symbol for known currencies', () => {
    expect(formatValue(2400000, 'EUR')).toBe('€2,400,000');
    expect(formatValue(380000, 'CAD')).toBe('CA$380,000');
  });

  it('falls back to a currency code it does not know', () => {
    expect(formatValue(5000, 'XYZ')).toBe('5,000 XYZ');
    expect(formatValue(5000, null)).toBe('5,000');
  });

  it('says so when nothing was published, rather than showing zero', () => {
    expect(formatValue(null, 'EUR')).toBe('value not published');
  });
});

describe('safeHref refuses anything that is not http(s)', () => {
  it('allows ordinary web URLs', () => {
    expect(safeHref('https://ted.europa.eu/notice/1')).toBe('https://ted.europa.eu/notice/1');
  });

  it('blocks script and data URLs that React would otherwise render', () => {
    expect(safeHref('javascript:alert(1)')).toBeNull();
    expect(safeHref('JavaScript:alert(1)')).toBeNull();
    expect(safeHref('data:text/html,<script>alert(1)</script>')).toBeNull();
    expect(safeHref('vbscript:msgbox(1)')).toBeNull();
  });

  it('blocks other schemes and empty values', () => {
    expect(safeHref('ftp://x.test/a')).toBeNull();
    expect(safeHref('mailto:a@b.test')).toBeNull();
    expect(safeHref(null)).toBeNull();
    expect(safeHref('')).toBeNull();
    expect(safeHref('   ')).toBeNull();
  });
});

describe('score and label mapping', () => {
  it("bands the score colour on the engine's own thresholds", () => {
    // Previously hardcoded 70/40, which put a score of 40 in an amber pill next
    // to a red "Not fit" badge — two verdicts on one notice. The engine's
    // possible-fit band is 50.
    expect(scoreTone(70)).toBe('green');
    expect(scoreTone(69)).toBe('amber');
    expect(scoreTone(50)).toBe('amber');
    expect(scoreTone(49)).toBe('red');
    expect(scoreTone(40)).toBe('red');
  });

  it('takes the bands from the caller so it never drifts from the backend', () => {
    const bands = { good_fit: 80, possible_fit: 45 };
    expect(scoreTone(80, bands)).toBe('green');
    expect(scoreTone(79, bands)).toBe('amber');
    expect(scoreTone(45, bands)).toBe('amber');
    expect(scoreTone(44, bands)).toBe('red');
  });

  it('renders every known enum and passes through an unknown one', () => {
    expect(fitLabel('high_fit')).toBe('Excellent fit');
    expect(deploymentLabel('mandatory_on_premises')).toBe('Mandatory on-premises');
    expect(fitLabel('something_new')).toBe('something_new');
  });
});

describe('countryLabel', () => {
  it('turns ISO alpha-3 into a readable name', () => {
    expect(countryLabel('DEU')).toBe('Germany');
    expect(countryLabel('NLD')).toBe('Netherlands');
    expect(countryLabel('ROU')).toBe('Romania');
  });

  it('turns ISO alpha-2 into a readable name', () => {
    expect(countryLabel('GB')).toBe('United Kingdom');
    expect(countryLabel('AU')).toBe('Australia');
  });

  it('passes a name through untouched', () => {
    expect(countryLabel('Indonesia')).toBe('Indonesia');
    expect(countryLabel('Eastern and Southern Africa')).toBe('Eastern and Southern Africa');
  });

  it('never guesses at something it does not recognise', () => {
    expect(countryLabel('ZZZ')).toBe('ZZZ');
    expect(countryLabel('')).toBe('—');
    expect(countryLabel(null)).toBe('—');
  });

  it('is case-insensitive on codes', () => {
    expect(countryLabel('deu')).toBe('Germany');
  });
});

describe('relativeTime', () => {
  const now = new Date('2026-08-21T12:00:00Z');

  it('describes the recent past the way a person would', () => {
    expect(relativeTime('2026-08-21T11:00:00Z', now)).toBe('1 hour ago');
    expect(relativeTime('2026-08-21T09:00:00Z', now)).toBe('3 hours ago');
    expect(relativeTime('2026-08-20T12:00:00Z', now)).toBe('yesterday');
  });

  it('describes the near future', () => {
    expect(relativeTime('2026-08-21T18:00:00Z', now)).toBe('in 6 hours');
  });

  it('collapses anything within the last minute', () => {
    expect(relativeTime('2026-08-21T11:59:50Z', now)).toBe('just now');
  });

  it('says never rather than inventing a date', () => {
    expect(relativeTime(null, now)).toBe('never');
    expect(relativeTime('not a date', now)).toBe('never');
  });
});

/* ---------------------------------------------------------------------------
 * Source health during a sweep
 *
 * The summary read "0 of 8 sources healthy" while a sweep was running, which is
 * the most alarming thing on the page and was false: every source had just been
 * set to `queued`/`running`, and those fell through runTone() to 'idle', which
 * the healthy count does not include. A system reporting zero healthy sources
 * *because it is working* teaches the reader to distrust it.
 * --------------------------------------------------------------------------- */
describe('sourceHealth separates working from broken', () => {
  const base = {
    enabled: true,
    unavailable_reason: null as string | null,
    running: false,
    last_status: null as string | null,
  };

  it('a source mid-sweep is sweeping, not unhealthy', () => {
    expect(sourceHealth({ ...base, last_status: 'running' })).toBe('sweeping');
    expect(sourceHealth({ ...base, last_status: 'queued' })).toBe('sweeping');
  });

  it('trusts the live running flag even before the run row updates', () => {
    expect(sourceHealth({ ...base, running: true, last_status: 'success' })).toBe('sweeping');
  });

  it('reports genuine states unchanged', () => {
    expect(sourceHealth({ ...base, last_status: 'success' })).toBe('good');
    expect(sourceHealth({ ...base, last_status: 'partial' })).toBe('warning');
    expect(sourceHealth({ ...base, last_status: 'failed' })).toBe('critical');
  });

  it('a missing credential is critical however the last run went', () => {
    expect(sourceHealth({ ...base, unavailable_reason: 'SAM_GOV_API_KEY is not set' })).toBe(
      'critical',
    );
  });

  it('a switched-off source is idle, not broken', () => {
    expect(sourceHealth({ ...base, enabled: false, last_status: 'success' })).toBe('idle');
  });

  it('never run yet is idle rather than healthy', () => {
    expect(sourceHealth({ ...base, last_status: null })).toBe('idle');
  });
});

describe('sweepSummary reports what a sweep did, in the reader’s words', () => {
  it('counts a finished sweep honestly, including the window it searched', () => {
    expect(
      sweepSummary({ created: 12, updated: 30, received: 303, daysBack: 30, done: true }),
    ).toBe('12 new, 30 updated from 303 notices seen across the last 30 days.');
  });

  it('does not claim nothing was found when nothing was new but plenty was seen', () => {
    // The old page said nothing at all here, which is what "it is not fetching"
    // actually looked like. Seen-but-already-known is a real, sayable outcome.
    expect(sweepSummary({ created: 0, updated: 19, received: 303, daysBack: 30, done: true })).toBe(
      'No new notices. 303 seen across the last 30 days were already stored, 19 updated.',
    );
  });

  it('says so when a source genuinely returned nothing at all', () => {
    expect(sweepSummary({ created: 0, updated: 0, received: 0, daysBack: 7, done: true })).toBe(
      'No notices at all were returned for the last 7 days. Check source health below.',
    );
  });

  it('reads as progress while still running', () => {
    expect(sweepSummary({ created: 4, updated: 2, received: 88, daysBack: 30, done: false })).toBe(
      'Sweeping the last 30 days… 88 seen so far, 4 new.',
    );
  });

  it('uses singular units where a person would', () => {
    expect(sweepSummary({ created: 1, updated: 0, received: 1, daysBack: 1, done: true })).toBe(
      '1 new, 0 updated from 1 notice seen across the last 1 day.',
    );
  });
});

describe('isSweepInFlight', () => {
  it('counts a queued batch as still going, not as finished', () => {
    // Every FetchRun row is born `queued`, so a batch reads queued for its first
    // instant. Testing only for `running` stopped the page polling before the
    // sweep had begun, freezing the progress it had just promised to track.
    expect(isSweepInFlight('queued')).toBe(true);
    expect(isSweepInFlight('running')).toBe(true);
  });

  it('a settled batch is not in flight', () => {
    for (const status of ['success', 'partial', 'failed', 'skipped', 'unknown']) {
      expect(isSweepInFlight(status)).toBe(false);
    }
  });

  it('no batch at all is not in flight', () => {
    expect(isSweepInFlight(null)).toBe(false);
    expect(isSweepInFlight(undefined)).toBe(false);
  });
});
