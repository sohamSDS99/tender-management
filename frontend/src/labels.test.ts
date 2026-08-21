import { describe, expect, it } from 'vitest';
import {
  deadlineUrgency,
  deploymentLabel,
  fitLabel,
  formatValue,
  safeHref,
  scoreTone,
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
  it('bands the score colour', () => {
    expect(scoreTone(70)).toBe('green');
    expect(scoreTone(69)).toBe('amber');
    expect(scoreTone(40)).toBe('amber');
    expect(scoreTone(39)).toBe('red');
  });

  it('renders every known enum and passes through an unknown one', () => {
    expect(fitLabel('high_fit')).toBe('Excellent fit');
    expect(deploymentLabel('mandatory_on_premises')).toBe('Mandatory on-premises');
    expect(fitLabel('something_new')).toBe('something_new');
  });
});
