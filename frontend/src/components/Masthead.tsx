import type { AutomationStatus, Stats, Theme } from '../types';
import { SWEEP_DEPTHS, formatDateTime, isSweepInFlight } from '../labels';
import { Icon } from './Icon';

/**
 * The topbar: identity, when the data last changed, and the two actions that act
 * on the whole system.
 *
 * Both actions were impossible until D23. They required `CRON_SECRET`, which the
 * browser is never given, so the only honest thing the old UI could do was leave
 * them out. The secret has been replaced by server-side cost controls — one sweep
 * at a time, and a cooldown — so these buttons now do what they say.
 *
 * Neither is destructive and neither needs a confirm: a sweep only adds and
 * updates (every write is an upsert keyed on source + notice id), and a re-score
 * recomputes a deterministic function of data already stored. The server refuses
 * with a readable reason when either is too soon.
 *
 * **The depth sits beside the button, not in Settings.** It is the one parameter
 * that decides whether pressing Fetch can find anything at all: the schedule
 * already sweeps the last 72 hours twice a day, so a button searching that same
 * window truthfully reports success and returns nothing. Putting the number out
 * of sight is what let that go unnoticed for as long as it did — "no new tenders
 * over 3 days" and "over 90 days" are different facts, and only the second is
 * worth acting on.
 */
export function Masthead({
  automation,
  stats,
  theme,
  busy,
  sweepDays,
  onSweepDays,
  onFetch,
  onRescore,
  onToggleTheme,
}: {
  automation: AutomationStatus | null;
  stats: Stats | null;
  theme: 'light' | 'dark';
  /** Which action is in flight, so only that button shows a pending label. */
  busy: 'fetch' | 'rescore' | null;
  /** Days of history the next sweep will search. */
  sweepDays: number;
  onSweepDays: (days: number) => void;
  onFetch: () => void;
  onRescore: () => void;
  onToggleTheme: () => void;
  /** The stored preference, for the toggle's label. */
  preference?: Theme;
}) {
  const lastRun = automation?.last_run;
  // Shared helper, so the button's idea of "still sweeping" can never drift
  // from the one driving the progress poll.
  const sweeping = busy === 'fetch' || isSweepInFlight(lastRun?.status);

  return (
    <header className="topbar">
      <div className="brandline">
        <div className="logo" aria-hidden="true">
          TM
        </div>
        <div>
          <h1>Tender Monitor</h1>
          <p>SDS management · authoring · chemical compliance · EHS software opportunities</p>
        </div>
      </div>

      <div className="topbar__actions">
        <div className="lastfetch">
          {stats?.last_successful_fetch ? (
            <>
              Last successful fetch
              <b>{formatDateTime(stats.last_successful_fetch)}</b>
            </>
          ) : (
            <>
              No successful fetch yet
              <b>—</b>
            </>
          )}
        </div>

        <div className="depth">
          <span className="depth__label" id="sweepDepthLabel">
            Search back
          </span>
          <div className="seg seg--sm" role="group" aria-labelledby="sweepDepthLabel">
            {SWEEP_DEPTHS.map((days) => (
              <button
                key={days}
                type="button"
                className={sweepDays === days ? 'is-on' : undefined}
                aria-pressed={sweepDays === days}
                disabled={sweeping}
                // The unit is on every option rather than only the group label:
                // a bare "7" beside a Fetch button could be a count of anything.
                aria-label={`Search back ${days} days`}
                onClick={() => onSweepDays(days)}
              >
                {days}d
              </button>
            ))}
          </div>
        </div>

        <button
          type="button"
          className="btn"
          disabled={busy !== null}
          onClick={onRescore}
          title="Reload the relevance profile and re-score every stored notice"
        >
          <Icon name="refresh" size={14} />
          {busy === 'rescore' ? 'Re-scoring…' : 'Re-score'}
        </button>

        <button
          type="button"
          className="btn btn--primary"
          disabled={busy !== null || sweeping}
          onClick={onFetch}
          title={
            sweeping
              ? 'A sweep is already running — watch its progress below'
              : `Query every enabled source for notices from the last ${sweepDays} days`
          }
        >
          <Icon name="download" size={14} />
          {sweeping ? 'Sweeping…' : `Fetch last ${sweepDays} days`}
        </button>

        <button
          type="button"
          className="btn btn--icon"
          onClick={onToggleTheme}
          aria-label={`Switch to ${theme === 'dark' ? 'light' : 'dark'} theme`}
          title={`Switch to ${theme === 'dark' ? 'light' : 'dark'} theme`}
        >
          <Icon name={theme === 'dark' ? 'sun' : 'moon'} size={16} />
        </button>
      </div>
    </header>
  );
}
