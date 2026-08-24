import { useEffect, useState } from 'react';
import { ApiError, api } from '../api/client';
import type { AutomationStatus } from '../types';
import { Icon } from './Icon';

/**
 * Setting the times the sweep runs.
 *
 * The sweep is automated, but *when* it runs is a human decision — so this is the
 * one place in the app a reader can change something. The person making the
 * change is the authorisation (docs/DECISIONS.md D19); there is no secret to
 * hold and no account to log into.
 *
 * The hours are local to the configured timezone. The UTC cron equivalents are
 * shown because that is what a scheduled GitHub Actions run would need, and
 * seeing "00:00 Dhaka = 18:00 UTC the previous day" written out is the only way
 * that mapping is ever obviously right.
 */
const HOURS = Array.from({ length: 24 }, (_, hour) => hour);

const label = (hour: number) => `${String(hour).padStart(2, '0')}:00`;

export function ScheduleEditor({
  automation,
  onSaved,
}: {
  automation: AutomationStatus | null;
  onSaved: () => void;
}) {
  const [draft, setDraft] = useState<number[]>([]);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  // Re-seed whenever the server's view changes, so the draft never drifts from
  // what is actually running.
  useEffect(() => {
    if (automation) setDraft(automation.run_hours_local);
  }, [automation]);

  if (!automation) return null;

  const min = automation.run_hours_min;
  const max = automation.run_hours_max;
  const current = automation.run_hours_local;
  const changed = draft.length !== current.length || draft.some((hour, i) => hour !== current[i]);

  const toggle = (hour: number) => {
    setSaved(false);
    setError(null);
    setDraft((prev) =>
      prev.includes(hour) ? prev.filter((h) => h !== hour) : [...prev, hour].sort((a, b) => a - b),
    );
  };

  const save = async () => {
    setSaving(true);
    setError(null);
    try {
      await api.setSchedule(draft);
      setSaved(true);
      onSaved();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setSaving(false);
    }
  };

  const tooFew = draft.length < min;
  const tooMany = draft.length > max;

  return (
    <div className="schedule">
      <h3>
        Sweep times
        <span className="muted"> · {automation.timezone}</span>
      </h3>
      <p className="schedule__hint">
        The sweep runs automatically at the times you pick here. Choose between {min} and {max} a
        day.
      </p>

      <div
        className="schedule__grid"
        role="group"
        aria-label={`Sweep times, ${automation.timezone}`}
      >
        {HOURS.map((hour) => {
          const on = draft.includes(hour);
          return (
            <button
              key={hour}
              type="button"
              className={`hour${on ? ' is-on' : ''}`}
              aria-pressed={on}
              onClick={() => toggle(hour)}
            >
              {label(hour)}
            </button>
          );
        })}
      </div>

      <p className="schedule__utc">
        {draft.length > 0 ? (
          <>
            {draft.map(label).join(', ')} {automation.timezone} — in UTC that is{' '}
            <span className="mono">{automation.cron_utc.join('  ')}</span>
            {changed ? <span className="muted"> (not saved yet)</span> : null}
          </>
        ) : (
          <span className="muted">Pick at least one time.</span>
        )}
      </p>

      {error ? (
        <p className="schedule__msg schedule__msg--bad" role="alert">
          <Icon name="warn" size={13} />
          {error}
        </p>
      ) : null}
      {saved && !changed ? (
        <p className="schedule__msg schedule__msg--ok" role="status">
          <Icon name="check" size={13} />
          Saved. The next sweep is {automation.next_run_local_label} {automation.timezone}.
        </p>
      ) : null}

      <div className="schedule__actions">
        <button
          type="button"
          className="btn btn--primary"
          disabled={!changed || saving || tooFew || tooMany}
          onClick={() => void save()}
        >
          {saving ? 'Saving…' : 'Save sweep times'}
        </button>
        {changed ? (
          <button type="button" className="btn btn--quiet" onClick={() => setDraft(current)}>
            Discard
          </button>
        ) : null}
        {tooFew ? <span className="schedule__note">Pick at least {min}.</span> : null}
        {tooMany ? <span className="schedule__note">That is more than {max} a day.</span> : null}
      </div>
    </div>
  );
}
