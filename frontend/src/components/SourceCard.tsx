import type { SourceStatus } from '../types';
import { formatTime, sourceHealth, type SourceHealth } from '../labels';

export const SOURCE_CARD_CLASS: Record<SourceHealth, string> = {
  good: ' src--good',
  warning: ' src--warning',
  critical: ' src--critical',
  idle: ' src--idle',
  sweeping: ' src--sweeping',
};

/**
 * One connector, with its state said in words as well as colour.
 *
 * Shared by the dashboard's collapsed health strip and the Sources settings
 * page. They answer different questions — the strip is "where is this data
 * coming from" on arrival (D20), the page is "what is wrong and how do I fix
 * it" — but a source card is a source card, and two copies would drift the
 * first time one of them learned something the other did not.
 */
export function SourceCard({
  source,
  busySource,
  onFetch,
  detailed = false,
}: {
  source: SourceStatus;
  /** Name of the source currently fetching, so only its button is pending. */
  busySource: string | null;
  onFetch: (name: string) => void;
  /** The settings page shows the notes and the last success; the strip does not. */
  detailed?: boolean;
}) {
  const state = sourceHealth(source);
  const status = source.unavailable_reason
    ? 'unavailable'
    : state === 'sweeping'
      ? 'sweeping now'
      : (source.last_status ?? 'never run');

  return (
    <article className={`src${SOURCE_CARD_CLASS[state]}`}>
      <header>
        <span className="src__name">{source.display_name}</span>
        <span className="src__status">{status}</span>
      </header>

      <p className="src__meta">
        {source.tender_count.toLocaleString('en-GB')} stored
        {source.last_run_at ? ` · ${formatTime(source.last_run_at)}` : ' · never run'}
        {source.keyword_prefiltered ? ' · keyword prefilter applied' : ''}
      </p>

      {source.unavailable_reason ? (
        <p className="src__err">{source.unavailable_reason}</p>
      ) : source.last_error ? (
        <p className="src__err">{source.last_error.slice(0, 160)}</p>
      ) : null}

      {detailed ? (
        <>
          {source.notes ? <p className="src__notes">{source.notes}</p> : null}
          <p className="src__meta">
            {source.last_success_at
              ? `Last successful run ${formatTime(source.last_success_at)}`
              : 'No successful run yet'}
            {source.requires_api_key ? ' · needs an API key' : ''}
            {!source.enabled ? ' · switched off in configuration' : ''}
          </p>
        </>
      ) : null}

      <div className="src__foot">
        <button
          type="button"
          className="btn btn--sm"
          disabled={
            Boolean(source.unavailable_reason) ||
            !source.enabled ||
            source.running ||
            busySource !== null
          }
          onClick={() => onFetch(source.name)}
          title={
            source.unavailable_reason
              ? 'This source cannot run until its configuration is fixed'
              : `Query ${source.display_name} now`
          }
        >
          {source.running || busySource === source.name ? 'Fetching…' : 'Fetch this source'}
        </button>
        {detailed ? (
          <a className="src__home" href={source.homepage} target="_blank" rel="noreferrer noopener">
            Open {source.display_name}
          </a>
        ) : null}
      </div>
    </article>
  );
}
