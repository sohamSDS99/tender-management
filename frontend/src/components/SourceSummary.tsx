import type { SourceStatus, TenderFilters } from '../types';
import { runTone } from '../labels';

/**
 * Where the tenders come from, and how many from each — at the top of the page.
 *
 * These are *stored* totals across everything ever ingested, which is a different
 * question from the filtered list below, so the heading says so. Clicking one
 * filters to that source, which makes the number act on the thing it describes.
 *
 * A source that cannot run (no API key) still shows its stored count, because
 * "SAM.gov: 1 notice, currently unavailable" is a truer statement than hiding it.
 */
const PIP = { good: '', warning: ' pip--warn', critical: ' pip--bad', idle: ' pip--idle' } as const;

export function SourceSummary({
  sources,
  filters,
  loading,
  onToggleSource,
}: {
  sources: SourceStatus[];
  filters: TenderFilters;
  loading: boolean;
  onToggleSource: (name: string) => void;
}) {
  if (loading && sources.length === 0) {
    return (
      <section className="srcbar" aria-label="Sources">
        <span className="sk" style={{ height: 13, width: 160 }} />
      </section>
    );
  }
  if (sources.length === 0) return null;

  const total = sources.reduce((sum, s) => sum + s.tender_count, 0);
  const ranked = [...sources].sort((a, b) => b.tender_count - a.tender_count);

  const tone = (source: SourceStatus) => {
    if (source.unavailable_reason) return 'critical' as const;
    if (!source.enabled) return 'idle' as const;
    return runTone(source.last_status);
  };

  return (
    <section className="srcbar" aria-label="Tenders stored by source">
      <h2 className="srcbar__label">{total.toLocaleString('en-GB')} stored, from</h2>
      <ul className="srcbar__list">
        {ranked.map((source) => {
          const selected = filters.sources.includes(source.name);
          const reason = source.unavailable_reason;
          return (
            <li key={source.name}>
              <button
                type="button"
                className={`srcpill${selected ? ' is-on' : ''}`}
                aria-pressed={selected}
                title={
                  reason ? `${source.display_name} — ${reason}` : `Show only ${source.display_name}`
                }
                onClick={() => onToggleSource(source.name)}
              >
                <span className={`pip${PIP[tone(source)]}`} aria-hidden="true" />
                {source.display_name}
                <b className="num">{source.tender_count.toLocaleString('en-GB')}</b>
              </button>
            </li>
          );
        })}
      </ul>
    </section>
  );
}
