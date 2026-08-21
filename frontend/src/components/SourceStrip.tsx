import type { SourceStatus } from '../types';
import { formatTime, pluralise, runTone } from '../labels';
import { Icon } from './Icon';

/**
 * Source health as one line that expands to per-source cards (delta 7).
 *
 * The mockup put a "Fetch this source" button on each card. Requirement 1
 * deletes it: fetching is automated and POST /api/fetch now needs the shared
 * secret. The cards report instead - status, counts, last sweep, and the exact
 * reason a source is unavailable.
 */
export interface SourceStripProps {
  sources: SourceStatus[];
  open: boolean;
  onToggle: () => void;
  loading: boolean;
}

function tone(source: SourceStatus): 'good' | 'warning' | 'critical' | 'idle' {
  if (source.unavailable_reason) return 'critical';
  if (!source.enabled) return 'idle';
  return runTone(source.last_status);
}

function summary(source: SourceStatus): string {
  const parts = [`${source.tender_count.toLocaleString('en-GB')} stored`];
  if (source.last_run_at) parts.push(formatTime(source.last_run_at));
  else parts.push('never run');
  if (source.keyword_prefiltered) parts.push('keyword prefilter applied');
  return parts.join(' · ');
}

export function SourceStrip({ sources, open, onToggle, loading }: SourceStripProps) {
  if (loading && sources.length === 0) {
    return (
      <section className="sources" aria-label="Source health">
        <div className="sources__bar" aria-busy="true">
          <span className="sk sk--s" style={{ width: 220, height: 13 }} />
        </div>
      </section>
    );
  }
  if (sources.length === 0) return null;

  const unhealthy = sources.filter((s) => tone(s) === 'critical');
  const degraded = sources.filter((s) => tone(s) === 'warning');
  const healthy = sources.length - unhealthy.length - degraded.length;
  const lastSweep = sources
    .map((s) => s.last_run_at)
    .filter((v): v is string => Boolean(v))
    .sort()
    .pop();

  const notes: string[] = [];
  unhealthy.forEach((s) => notes.push(`${s.display_name} unavailable`));
  degraded.forEach((s) => notes.push(`${s.display_name} partial`));

  return (
    <section className={`sources${open ? ' is-open' : ''}`} aria-label="Source health">
      <button
        type="button"
        className="sources__bar"
        onClick={onToggle}
        aria-expanded={open}
        aria-controls="sourcesGrid"
      >
        <span className="pips" aria-hidden="true">
          {sources.map((source) => {
            const t = tone(source);
            return (
              <i
                key={source.name}
                className={`pip${t === 'critical' ? ' pip--critical' : t === 'warning' ? ' pip--warning' : t === 'idle' ? ' pip--idle' : ''}`}
              />
            );
          })}
        </span>
        <span>
          <b>
            {healthy} of {sources.length} {pluralise(sources.length, 'source')} healthy
          </b>
          {notes.length ? ` · ${notes.join(' · ')}` : ''}
          {lastSweep ? ` · last sweep ${formatTime(lastSweep)}` : ' · no sweep yet'}
        </span>
        <Icon name="chevron" size={16} className="chev" />
      </button>

      <div className="sources__grid" id="sourcesGrid">
        {sources.map((source) => {
          const t = tone(source);
          return (
            <article key={source.name} className={`src src--${t}`}>
              <header>
                <a href={source.homepage} target="_blank" rel="noreferrer noopener">
                  {source.display_name}
                </a>
                <span className="src__status">
                  {source.unavailable_reason ? 'unavailable' : (source.last_status ?? 'idle')}
                </span>
              </header>
              <p className="src__meta">{summary(source)}</p>
              {source.unavailable_reason ? (
                <p className="src__err">{source.unavailable_reason}</p>
              ) : source.last_error ? (
                <p className="src__err">{source.last_error.slice(0, 200)}</p>
              ) : null}
              {source.notes ? <p className="src__meta">{source.notes}</p> : null}
            </article>
          );
        })}
      </div>
    </section>
  );
}
