import type { SourceStatus } from '../types';
import { formatTime, sourceHealth } from '../labels';
import { Icon } from './Icon';

const PIP = {
  good: '',
  warning: ' pip--warning',
  critical: ' pip--critical',
  idle: ' pip--idle',
  sweeping: ' pip--sweeping',
} as const;

const CARD = {
  good: ' src--good',
  warning: ' src--warning',
  critical: ' src--critical',
  idle: ' src--idle',
  sweeping: ' src--sweeping',
} as const;

const tone = sourceHealth;

/**
 * Source health, collapsed to one line until asked for.
 *
 * A source failing never fails the sweep — each one gets its own run row — so the
 * summary has to distinguish "some sources are unhealthy" from "the sweep broke",
 * and name which and why. The per-source fetch button exists because a single
 * connector recovering is the common case after a key or an outage is fixed, and
 * re-running eight sources to test one is thirteen wasted minutes.
 */
export function SourcesPanel({
  sources,
  open,
  onToggle,
  lastSweepAt,
  busySource,
  onFetchSource,
}: {
  sources: SourceStatus[];
  open: boolean;
  onToggle: (open: boolean) => void;
  lastSweepAt: string | null;
  /** Name of the source currently being fetched, so only its button is pending. */
  busySource: string | null;
  onFetchSource: (name: string) => void;
}) {
  if (sources.length === 0) return null;

  const healthy = sources.filter((s) => tone(s) === 'good').length;
  const problems = sources.filter((s) => tone(s) === 'critical' || tone(s) === 'warning');
  // Counted separately so a sweep in progress can never read as a failure. This
  // line used to say "0 of 8 sources healthy" for the whole of a sweep, because
  // every source had just been set to `queued` and that is not `good`.
  const sweeping = sources.filter((s) => tone(s) === 'sweeping');

  return (
    <section className={`sources${open ? ' is-open' : ''}`} aria-label="Source health">
      <button
        type="button"
        className="sources__bar"
        aria-expanded={open}
        onClick={() => onToggle(!open)}
      >
        <span className="pips" aria-hidden="true">
          {sources.map((source) => (
            <i key={source.name} className={`pip${PIP[tone(source)]}`} />
          ))}
        </span>
        <span>
          {sweeping.length > 0 ? (
            <b>
              Sweeping {sweeping.length} of {sources.length} sources now
            </b>
          ) : (
            <b>
              {healthy} of {sources.length} sources healthy
            </b>
          )}
          {sweeping.length > 0 && healthy > 0 ? ` · ${healthy} already reported` : ''}
          {problems.length > 0
            ? ` · ${problems.map((s) => `${s.display_name} ${tone(s) === 'critical' ? 'unavailable' : 'partial'}`).join(' · ')}`
            : ''}
          {lastSweepAt ? ` · last sweep ${formatTime(lastSweepAt)}` : ' · never swept'}
        </span>
        <Icon name="chevronDown" size={16} className="chev" />
      </button>

      {open ? (
        <div className="sources__grid">
          {sources.map((source) => {
            const state = tone(source);
            const status = source.unavailable_reason
              ? 'unavailable'
              : state === 'sweeping'
                ? 'sweeping now'
                : (source.last_status ?? 'never run');
            return (
              <article key={source.name} className={`src${CARD[state]}`}>
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
                    onClick={() => onFetchSource(source.name)}
                    title={
                      source.unavailable_reason
                        ? 'This source cannot run until its configuration is fixed'
                        : `Query ${source.display_name} now`
                    }
                  >
                    {source.running || busySource === source.name
                      ? 'Fetching…'
                      : 'Fetch this source'}
                  </button>
                </div>
              </article>
            );
          })}
        </div>
      ) : null}
    </section>
  );
}
