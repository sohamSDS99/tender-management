import type { ReactNode } from 'react';
import type { AutomationStatus, FetchRun, SourceStatus } from '../types';
import { formatDateTime, formatTime, runTone } from '../labels';
import { Icon } from './Icon';

/**
 * Source health and recent sweeps — leadership's occasional glance.
 *
 * Collapsed at the bottom because a bidder never needs it, and the daily hunt
 * must not compete with it. Confirmed as a glance, not a workflow.
 */
const PIP = { good: '', warning: ' pip--warn', critical: ' pip--bad', idle: ' pip--idle' } as const;

export function SystemSection({
  open,
  onToggle,
  automation,
  sources,
  runs,
  schedule,
}: {
  open: boolean;
  onToggle: (open: boolean) => void;
  automation: AutomationStatus | null;
  sources: SourceStatus[];
  runs: FetchRun[];
  /** The sweep-time editor. Lives here because it is an operating control,
   *  not part of the daily hunt. */
  schedule?: ReactNode;
}) {
  if (sources.length === 0) return null;

  const tone = (source: SourceStatus) => {
    if (source.unavailable_reason) return 'critical' as const;
    if (!source.enabled) return 'idle' as const;
    return runTone(source.last_status);
  };
  const healthy = sources.filter((s) => tone(s) === 'good').length;

  return (
    <details
      className="system"
      open={open}
      onToggle={(event) => onToggle((event.currentTarget as HTMLDetailsElement).open)}
    >
      <summary>
        <Icon name="chevronRight" size={12} />
        {healthy} of {sources.length} sources healthy
        {automation?.cron_utc
          ? ` · sweeps at ${automation.run_hours_local.map((h) => `${String(h).padStart(2, '0')}:00`).join(' and ')} ${automation.timezone}`
          : ''}
      </summary>

      <div className="system__body">
        {schedule}

        <div>
          <div className="srcs">
            {sources.map((source) => (
              <div className="src" key={source.name}>
                <span className={`pip${PIP[tone(source)]}`} aria-hidden="true" />
                <span>
                  <span className="src__name">{source.display_name}</span>
                  {source.unavailable_reason ? (
                    <span className="src__note"> — {source.unavailable_reason}</span>
                  ) : source.last_error ? (
                    <span className="src__note"> — {source.last_error.slice(0, 120)}</span>
                  ) : source.last_run_at ? (
                    <span className="src__note">
                      {' '}
                      — last swept {formatTime(source.last_run_at)}
                    </span>
                  ) : (
                    <span className="src__note"> — never swept</span>
                  )}
                </span>
                <span className="src__n">{source.tender_count.toLocaleString('en-GB')}</span>
              </div>
            ))}
          </div>
        </div>

        {runs.length > 0 ? (
          <div>
            <table className="table">
              <thead>
                <tr>
                  <th>Source</th>
                  <th>Result</th>
                  <th>Seen</th>
                  <th>New</th>
                  <th>Started</th>
                </tr>
              </thead>
              <tbody>
                {runs.slice(0, 10).map((run) => (
                  <tr key={run.id}>
                    <td className="mono">{run.source}</td>
                    <td>{run.status}</td>
                    <td>{run.records_received.toLocaleString('en-GB')}</td>
                    <td>{run.records_created.toLocaleString('en-GB')}</td>
                    <td>{formatDateTime(run.started_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : null}
      </div>
    </details>
  );
}
