import type { AutomationStatus, Stats } from '../types';
import { formatDateTime } from '../labels';
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
 */
export function Masthead({
  automation,
  stats,
  busy,
  onFetch,
  onRescore,
}: {
  automation: AutomationStatus | null;
  stats: Stats | null;
  /** Which action is in flight, so only that button shows a pending label. */
  busy: 'fetch' | 'rescore' | null;
  onFetch: () => void;
  onRescore: () => void;
}) {
  const lastRun = automation?.last_run;
  const sweeping = lastRun?.status === 'running' || busy === 'fetch';

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
          title="Query all enabled sources now"
        >
          <Icon name="download" size={14} />
          {sweeping ? 'Sweeping…' : 'Fetch new tenders'}
        </button>
      </div>
    </header>
  );
}
