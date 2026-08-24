import { useEffect, useState } from 'react';
import { ApiError, api } from '../api/client';
import { formatDateTime } from '../labels';
import type { AutomationStatus } from '../types';
import { Icon } from './Icon';

/**
 * Switching the automated sweep on and off.
 *
 * The times live next door in ScheduleEditor; this is the coarser decision above
 * them — whether the sweep happens at all. It exists because the alternative was
 * editing `ENABLE_SCHEDULER` and recreating the container, so an operator facing
 * a rate-limiting source or a maintenance window had no way to stop the sweep
 * from the tool that runs it (docs/DECISIONS.md D21).
 *
 * There is no local draft. The state shown *is* the server's state, so this can
 * never sit there displaying a switch position that is not in force.
 *
 * Pausing asks twice; resuming does not. A pause is instantly reversible, but a
 * system that quietly stopped collecting is the kind of thing nobody notices for
 * a week — so the direction that can cost you tenders gets a confirm step.
 */
export function TriggerSwitch({
  automation,
  onSaved,
}: {
  automation: AutomationStatus | null;
  onSaved: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [confirming, setConfirming] = useState(false);

  const enabled = automation?.scheduler_in_process ?? false;

  // A stale confirm prompt after the state has already moved would offer to
  // pause something that is already paused.
  useEffect(() => {
    setConfirming(false);
  }, [enabled]);

  if (!automation) return null;

  const paused = !enabled;
  const hours = automation.run_hours_local
    .map((hour) => `${String(hour).padStart(2, '0')}:00`)
    .join(' and ');

  const change = async (next: boolean) => {
    setBusy(true);
    setError(null);
    try {
      await api.setTrigger(next);
      setConfirming(false);
      onSaved();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className={`trigger${paused ? ' is-paused' : ''}`}>
      <div className="trigger__row">
        <h3>Automated sweeps</h3>
        <span
          className={`trigger__state${paused ? ' trigger__state--off' : ''}`}
          aria-live="polite"
        >
          <Icon name={paused ? 'block' : 'check'} size={13} />
          {paused ? 'Paused' : 'On'}
        </span>

        <span className="trigger__actions">
          {paused ? (
            <button
              type="button"
              className="btn btn--primary"
              disabled={busy}
              onClick={() => void change(true)}
            >
              {busy ? 'Switching on…' : 'Switch sweeps on'}
            </button>
          ) : confirming ? (
            <>
              <button
                type="button"
                className="btn btn--danger"
                disabled={busy}
                onClick={() => void change(false)}
              >
                {busy ? 'Pausing…' : 'Yes, pause sweeps'}
              </button>
              <button type="button" className="btn btn--quiet" onClick={() => setConfirming(false)}>
                Keep them on
              </button>
            </>
          ) : (
            <button type="button" className="btn" onClick={() => setConfirming(true)}>
              Pause sweeps
            </button>
          )}
        </span>
      </div>

      {paused ? (
        <p className="trigger__hint trigger__hint--warn">
          Nothing is being collected and no Slack digest will be sent
          {automation.trigger_changed_at ? (
            <>. Paused since {formatDateTime(automation.trigger_changed_at)}</>
          ) : null}
          .
        </p>
      ) : (
        <p className="trigger__hint">
          Sweeping at {hours} {automation.timezone}. Next sweep {automation.next_run_local_label}.
        </p>
      )}

      {confirming && !busy ? (
        <p className="trigger__hint trigger__hint--warn">
          Sweeps stay off until someone switches them back on here. Notices published while paused
          are not backfilled beyond the normal lookback window.
        </p>
      ) : null}

      {/* Only one process may own the trigger, or the same window is fetched
          twice (D2). If the environment says off and this says on, the dashboard
          is the reason sweeps happen — worth saying, because the person who set
          it is not necessarily the person reading it. */}
      {enabled && !automation.trigger_default ? (
        <p className="trigger__hint">
          Switched on from here, not by configuration. If a GitHub Actions schedule also runs
          against this database, both would fetch the same window.
        </p>
      ) : null}

      {error ? (
        <p className="schedule__msg schedule__msg--bad" role="alert">
          <Icon name="warn" size={13} />
          {error}
        </p>
      ) : null}
    </div>
  );
}
