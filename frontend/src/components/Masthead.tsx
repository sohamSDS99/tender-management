import type { AutomationStatus } from '../types';
import { formatDateTime } from '../labels';
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
}: {
  automation: AutomationStatus | null;
  theme: 'light' | 'dark';
  onToggleTheme: () => void;
}) {
  const last = automation?.last_run ?? null;

  return (
    <header className="masthead">
      <h1>Tender Monitor</h1>

      <p className="masthead__status">
        {automation ? (
          <>
            Next sweep <b>{automation.next_run_local_label}</b> Dhaka
            {last ? (
              <>
                {' · '}last found <b>{last.records_created.toLocaleString('en-GB')}</b> new
                {last.sources_failed > 0 ? (
                  <>
                    {' '}
                    from {last.sources_total - last.sources_failed} of {last.sources_total} sources
                  </>
                ) : null}
                {' · '}
                {formatDateTime(last.started_at)}
              </>
            ) : (
              ' · no sweep yet'
            )}
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
