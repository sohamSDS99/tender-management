import type { SourceStatus } from '../types';
import { formatWhen, sourceHealth, type SourceVolume } from '../labels';
import { SOURCE_CARD_CLASS } from './SourceCard';
import { Icon } from './Icon';

/**
 * One source, on its own, with the list below showing only what it brought.
 *
 * The question this answers is "what did *this* feed actually give us", which
 * neither of the other two source surfaces could. The dashboard strip says
 * where the data comes from and the settings page says what is broken; both
 * report a stored count and neither could show you the notices behind it. A
 * count you cannot open is a claim you cannot check — and with eleven sources,
 * "is this one earning its place" is a question worth being able to ask.
 *
 * Two numbers, deliberately: what the source has ever stored, and how many of
 * those the filters currently on screen let through. They differ whenever a
 * lens or a score floor is applied, and collapsing them into one would make
 * whichever survived read as the other.
 */
/**
 * The one source being looked at, or null.
 *
 * Null for two or more, deliberately. The panel states one source's stored
 * count beside the number of rows on screen, and with two selected that
 * sentence is false about both of them - it would name the first and describe a
 * list belonging to both. A list of two sources has no single source to head
 * it, and the chip row already says which two they are.
 */
export function focusedSource(sources: SourceStatus[], selected: string[]): SourceStatus | null {
  if (selected.length !== 1) return null;
  return sources.find((source) => source.name === selected[0]) ?? null;
}

export function SourceFocus({
  source,
  volume,
  shown,
  busySource,
  onFetch,
  onClear,
}: {
  source: SourceStatus;
  volume?: SourceVolume;
  /** How many of this source's notices the current filters admit. */
  shown: number;
  busySource: string | null;
  onFetch: (name: string) => void;
  onClear: () => void;
}) {
  const state = sourceHealth(source, volume);
  const status = source.unavailable_reason
    ? 'unavailable'
    : state === 'sweeping'
      ? 'sweeping now'
      : state === 'quiet'
        ? 'quiet'
        : (source.last_status ?? 'never run');
  const stored = source.tender_count;

  return (
    <section
      className={`srcfocus${SOURCE_CARD_CLASS[state]}`}
      aria-label={`${source.display_name} source`}
    >
      <header className="srcfocus__head">
        <div className="srcfocus__title">
          <h2>{source.display_name}</h2>
          <span className="src__status">{status}</span>
        </div>
        <button type="button" className="btn btn--ghost btn--sm" onClick={onClear}>
          All sources
        </button>
      </header>

      <p className="srcfocus__meta">
        <b className="num">{stored.toLocaleString('en-GB')}</b>{' '}
        {stored === 1 ? 'notice' : 'notices'} stored from this source
        {/* Only worth saying when the two differ; otherwise it is noise that
            makes an unfiltered view look filtered. */}
        {shown !== stored ? (
          <>
            {' · '}
            <b className="num">{shown.toLocaleString('en-GB')}</b> shown under the filters on screen
          </>
        ) : null}
        {source.last_run_at
          ? ` · last queried ${formatWhen(source.last_run_at)}`
          : ' · never queried'}
        {source.last_success_at
          ? ` · last succeeded ${formatWhen(source.last_success_at)}`
          : ' · no successful run yet'}
        {source.keyword_prefiltered ? ' · keyword prefilter applied' : ''}
      </p>

      {source.notes ? <p className="srcfocus__notes">{source.notes}</p> : null}

      {state === 'quiet' && volume?.verdict === 'quiet' ? (
        <p className="src__quiet">
          Returned nothing in its last {volume.zeros} scheduled sweeps. It normally returns about{' '}
          {volume.typical.toLocaleString('en-GB')} notices.
        </p>
      ) : null}

      {/* Same rule the card follows: a current problem is an alarm, a past one
          is a fact. A stored key clears unavailable_reason but the skipped run
          that predates it stays on record forever. */}
      {source.unavailable_reason ? (
        <p className="src__err">{source.unavailable_reason}</p>
      ) : source.last_error ? (
        <p className="src__was">
          {source.last_run_at ? `${formatWhen(source.last_run_at)}: ` : ''}
          {source.last_error.slice(0, 200)}
        </p>
      ) : null}

      {stored === 0 && !source.unavailable_reason ? (
        <p className="srcfocus__empty">
          <Icon name="warn" size={14} />
          <span>
            Nothing stored from this source yet. Fetch it below, or widen the sweep depth — a source
            that has never run has nothing to show, and one that ran and found nothing is a
            different problem.
          </span>
        </p>
      ) : null}

      <div className="srcfocus__foot">
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
        {source.homepage ? (
          <a className="src__home" href={source.homepage} target="_blank" rel="noreferrer noopener">
            Open {source.display_name}
          </a>
        ) : null}
      </div>
    </section>
  );
}
