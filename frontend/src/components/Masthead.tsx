import type { AutomationStatus } from '../types';
import { relativeTime } from '../labels';
import { Icon } from './Icon';

/**
 * One line: what this is, what the automation is doing, and the theme control.
 *
 * The predecessor spent a bordered card and five metric tiles here. Automation
 * status is a sentence because that is what it is — there is nothing to interact
 * with, and nothing a bidder needs to do about it.
 */
export function Masthead({
  automation,
  theme,
  onToggleTheme,
  onEditSchedule,
}: {
  automation: AutomationStatus | null;
  theme: 'light' | 'dark';
  onToggleTheme: () => void;
  /** Opens the sweep-time editor, the only control that changes anything. */
  onEditSchedule: () => void;
}) {
  const last = automation?.last_run ?? null;

  return (
    <header className="masthead">
      <h1>Tender Monitor</h1>

      <p className="masthead__status">
        {automation ? (
          <>
            {last ? (
              <>
                Last swept <b>{relativeTime(last.started_at)}</b>, found{' '}
                <b>{last.records_created.toLocaleString('en-GB')}</b> new
                {last.sources_failed > 0 ? (
                  <>
                    {' '}
                    from {last.sources_total - last.sources_failed} of {last.sources_total} sources
                  </>
                ) : null}
              </>
            ) : (
              <>No sweep yet</>
            )}
            {' · next '}
            <button
              type="button"
              className="linkish"
              title={`${automation.next_run_local_label} ${automation.timezone} — click to change the sweep times`}
              onClick={onEditSchedule}
            >
              {relativeTime(automation.next_run_at)}
            </button>
          </>
        ) : (
          'Checking automation…'
        )}
      </p>

      <div className="masthead__actions">
        <button
          type="button"
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
