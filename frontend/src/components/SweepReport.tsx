import type { FetchRun, LastRun } from '../types';
import { isSweepInFlight, pluralise, sweepSummary } from '../labels';
import { Icon } from './Icon';

/**
 * What the sweep you just started is doing, and what it found.
 *
 * This is the missing half of the "fetching does not work" defect. The page said
 * "Sweep started across 7 sources" and then never mentioned it again — so a sweep
 * that stored eight notices and a sweep that stored none produced identical
 * screens, and the only visible list was filtered to score ≥ 70, which no real
 * notice has ever reached. The work happened; the page simply never reported it.
 *
 * Three rules this panel follows:
 *
 * 1. **A number you cannot check is not worth printing.** The new-notice count is
 *    a button that filters the list to exactly those notices.
 * 2. **"Nothing new" is not the same as "nothing found".** 303 notices seen and
 *    already stored is a completely different fact from 303 not returned at all,
 *    and only the second one is a fault. They get different sentences.
 * 3. **The window is always stated.** "Found nothing in the last 3 days" and "in
 *    the last 90 days" call for different actions.
 *
 * It disappears on dismissal only. A sweep takes minutes and the reader is
 * expected to look away and come back.
 */
export function SweepReport({
  daysBack,
  batchId,
  lastRun,
  runs,
  onShowNew,
  onDismiss,
}: {
  /** The window this sweep was told to search. */
  daysBack: number;
  /** Which batch is ours, so another process's sweep is never reported as it. */
  batchId: string | null;
  lastRun: LastRun | null;
  runs: FetchRun[];
  onShowNew: () => void;
  onDismiss: () => void;
}) {
  // Only report on our own sweep. Without this the panel would narrate whatever
  // /api/automation last saw, including a scheduled run that started meanwhile.
  const mine = lastRun && batchId && lastRun.batch_id === batchId ? lastRun : null;
  const ourRuns = batchId ? runs.filter((run) => run.batch_id === batchId) : [];

  const done = mine ? !isSweepInFlight(mine.status) : false;
  const received = mine?.records_received ?? 0;
  const created = mine?.records_created ?? 0;
  const updated = mine?.records_updated ?? 0;
  const total = mine?.sources_total ?? ourRuns.length;
  const finished = ourRuns.filter((run) => run.finished_at !== null).length;
  const failed = mine?.sources_failed ?? 0;

  // Starting state: the POST has returned but /api/automation has not caught up.
  const pending = mine === null;
  const progress = total > 0 ? Math.round((finished / total) * 100) : 0;

  const tone = done ? (created > 0 ? 'good' : 'quiet') : 'busy';

  return (
    <section className={`sweep sweep--${tone}`} aria-label="Sweep progress" aria-live="polite">
      <div className="sweep__head">
        <span className="sweep__icon" aria-hidden="true">
          <Icon name={done ? (created > 0 ? 'check' : 'info') : 'download'} size={15} />
        </span>
        <p className="sweep__line">
          {pending
            ? `Sweep started across the last ${daysBack} ${pluralise(daysBack, 'day')}. Reading the sources now…`
            : sweepSummary({ created, updated, received, daysBack, done })}
        </p>
        <button
          type="button"
          className="btn btn--icon btn--sm"
          aria-label="Dismiss the sweep report"
          title="Dismiss"
          onClick={onDismiss}
        >
          <Icon name="close" size={14} />
        </button>
      </div>

      {/* Static width per render, never a width transition: DESIGN.md forbids
          animating layout properties, and this is the same meter idiom the
          detail panel's subscores use. */}
      {!done ? (
        <div
          className="sweep__track"
          role="meter"
          aria-valuenow={finished}
          aria-valuemin={0}
          aria-valuemax={Math.max(total, 1)}
          aria-label={`Sources finished: ${finished} of ${total}`}
        >
          <div className="sweep__fill" style={{ width: `${progress}%` }} />
        </div>
      ) : null}

      <p className="sweep__meta">
        {done ? (
          <>
            {total} {pluralise(total, 'source')} swept
            {failed > 0 ? (
              <>
                {' · '}
                <span className="sweep__bad">{failed} failed — the rest came through</span>
              </>
            ) : null}
          </>
        ) : (
          <>
            {finished} of {total} {pluralise(total, 'source')} finished · a full sweep takes several
            minutes and this page keeps up on its own
          </>
        )}
      </p>

      {done && created > 0 ? (
        <div className="sweep__actions">
          {/* Every number on this page is a filter. This one narrows the list to
              exactly the notices the count refers to — at any score, because a
              real notice has never yet cleared the 70 the default view asks for,
              and hiding them behind that floor is what made the sweep look dead. */}
          <button type="button" className="btn btn--primary btn--sm" onClick={onShowNew}>
            Show the {created.toLocaleString('en-GB')} new {pluralise(created, 'notice')}
            <Icon name="chevronRight" size={13} />
          </button>
        </div>
      ) : null}
    </section>
  );
}
