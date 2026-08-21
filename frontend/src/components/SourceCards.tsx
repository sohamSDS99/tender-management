import { useState } from 'react';
import type { SourceStatus } from '../types';
import { formatDateTime } from '../labels';

function tone(source: SourceStatus): string {
  if (!source.enabled) return 'grey';
  if (source.unavailable_reason) return 'grey';
  if (source.running) return 'blue';
  if (source.last_status === 'failed') return 'red';
  if (source.last_status === 'partial') return 'amber';
  if (source.last_status === 'success') return 'green';
  return 'grey';
}

function statusText(source: SourceStatus): string {
  if (!source.enabled) return 'disabled';
  if (source.unavailable_reason) return 'unavailable';
  if (source.running) return 'fetching…';
  return source.last_status ?? 'never run';
}

export function SourceCards({ sources, loading }: { sources: SourceStatus[]; loading: boolean }) {
  const [open, setOpen] = useState(false);
  if (loading && sources.length === 0) {
    return <p className="muted">Loading source health…</p>;
  }
  return (
    <section className="sources" aria-label="Source health">
      <button className="sources__toggle" onClick={() => setOpen(!open)} aria-expanded={open}>
        {open ? '▾' : '▸'} Sources (
        {sources.filter((s) => s.enabled && !s.unavailable_reason).length}/{sources.length} active)
      </button>
      <div className={`sources__grid ${open ? 'is-open' : ''}`}>
        {sources.map((source) => (
          <article key={source.name} className={`source source--${tone(source)}`}>
            <header>
              <a href={source.homepage} target="_blank" rel="noreferrer">
                {source.display_name}
              </a>
              <span className="source__status">{statusText(source)}</span>
            </header>
            <p className="source__counts">
              {source.tender_count} stored · last run {formatDateTime(source.last_run_at)}
            </p>
            {open && (
              <>
                <p className="source__notes">{source.notes}</p>
                {source.keyword_prefiltered && (
                  <p className="source__notes muted">Keyword prefilter applied before storage.</p>
                )}
                {source.unavailable_reason && (
                  <p className="source__error">{source.unavailable_reason}</p>
                )}
                {source.last_error && !source.unavailable_reason && (
                  <p className="source__error">{source.last_error.slice(0, 300)}</p>
                )}
              </>
            )}
          </article>
        ))}
      </div>
    </section>
  );
}
