import type { SourceStatus } from '../types';
import { formatTime, sourceHealth, type SourceHealth, type SourceVolumes } from '../labels';
import { Icon } from './Icon';
import { SourceCard } from './SourceCard';

const PIP = {
  good: '',
  warning: ' pip--warning',
  critical: ' pip--critical',
  idle: ' pip--idle',
  sweeping: ' pip--sweeping',
  quiet: ' pip--quiet',
} as const;

/**
 * The one word the strip gives a source that needs a look.
 *
 * One word only, on purpose. The strip answers "where is this data coming
 * from"; the card answers "what is wrong and how much is missing".
 */
function problemWord(state: SourceHealth): string | null {
  if (state === 'critical') return 'unavailable';
  if (state === 'warning') return 'partial';
  if (state === 'quiet') return 'quiet';
  return null;
}

/**
 * Source health, collapsed to one line until asked for.
 *
 * Stays on the dashboard even though Settings now has a Sources page, because
 * the two answer different questions. This one is "where is this data coming
 * from", which a reader has on arrival (D20); the settings page is "something is
 * broken, what and why", which they go looking for. Both render the same
 * SourceCard, so neither can learn something the other does not.
 *
 * A source failing never fails the sweep — each one gets its own run row — so
 * the summary has to distinguish "some sources are unhealthy" from "the sweep
 * broke", and name which and why.
 */
export function SourcesPanel({
  sources,
  volumes,
  open,
  onToggle,
  lastSweepAt,
  busySource,
  onFetchSource,
  onOpenSource,
}: {
  sources: SourceStatus[];
  /** Per-source volume verdicts, so a silently-empty source is not painted green. */
  volumes: SourceVolumes;
  open: boolean;
  onToggle: (open: boolean) => void;
  lastSweepAt: string | null;
  /** Name of the source currently being fetched, so only its button is pending. */
  busySource: string | null;
  onFetchSource: (name: string) => void;
  /** Filter the list below to one source. */
  onOpenSource: (name: string) => void;
}) {
  if (sources.length === 0) return null;

  const tone = (source: SourceStatus): SourceHealth => sourceHealth(source, volumes[source.name]);

  const healthy = sources.filter((s) => tone(s) === 'good').length;
  const problems = sources
    .map((source) => ({ source, word: problemWord(tone(source)) }))
    .filter((entry): entry is { source: SourceStatus; word: string } => entry.word !== null);
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
            ? ` · ${problems.map((entry) => `${entry.source.display_name} ${entry.word}`).join(' · ')}`
            : ''}
          {lastSweepAt ? ` · last sweep ${formatTime(lastSweepAt)}` : ' · never swept'}
        </span>
        <Icon name="chevronDown" size={16} className="chev" />
      </button>

      {open ? (
        <div className="sources__grid">
          {sources.map((source) => (
            <SourceCard
              key={source.name}
              source={source}
              volume={volumes[source.name]}
              busySource={busySource}
              onFetch={onFetchSource}
              onOpen={onOpenSource}
            />
          ))}
        </div>
      ) : null}
    </section>
  );
}
