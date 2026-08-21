import type { AutomationStatus } from '../types';
import { formatDateTime, runTone } from '../labels';
import { Icon } from './Icon';

/**
 * Header on a light surface (delta 1), with the automation summary where the
 * "Fetch new tenders" button used to be (requirement 1).
 *
 * There is no control here that can start a fetch or write anything: the sweep
 * runs at 00:00 and 12:00 Asia/Dhaka on its own, and both write endpoints now
 * require a shared secret the browser must never hold.
 */
export interface TopBarProps {
  automation: AutomationStatus | null;
  /** Already resolved: 'system' is decided by the caller, not guessed here. */
  theme: 'light' | 'dark';
  onToggleTheme: () => void;
}

const TONE_CLASS: Record<string, string> = {
  good: 'dot--good',
  warning: 'dot--warning',
  critical: 'dot--critical',
  idle: 'dot--brand',
};

export function TopBar({ automation, theme, onToggleTheme }: TopBarProps) {
  const last = automation?.last_run ?? null;

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
        <div className="automation">
          <p className="autofact">
            Next automated run
            <b>{automation ? `${automation.next_run_local_label} (Dhaka)` : '—'}</b>
          </p>
          <p className="autofact autofact--run">
            Last run
            <b>
              {last ? (
                <>
                  <span className={`dot ${TONE_CLASS[runTone(last.status)]}`} />
                  {last.status} · {last.records_created} new
                </>
              ) : (
                'never run'
              )}
            </b>
            {last ? <span className="muted"> {formatDateTime(last.started_at)}</span> : null}
          </p>
        </div>
        <button
          className="btn btn--icon"
          onClick={onToggleTheme}
          title={`Switch to ${theme === 'dark' ? 'light' : 'dark'} theme`}
          aria-label={`Switch to ${theme === 'dark' ? 'light' : 'dark'} theme`}
        >
          <Icon name={theme === 'dark' ? 'sun' : 'moon'} size={16} />
        </button>
      </div>
    </header>
  );
}
