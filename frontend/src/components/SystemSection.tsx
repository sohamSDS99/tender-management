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

/** A bare IPv4 host, which a DHCP lease can move without warning. */
const BARE_IP = /^https?:\/\/\d{1,3}(\.\d{1,3}){3}(:\d+)?$/;

/**
 * Where Slack digest links point.
 *
 * Worth showing because it fails silently: if this base is wrong, every link
 * already sent is dead and nothing else on screen would say so. It happened
 * during development - the host's address moved from 192.168.1.5 to
 * 192.168.0.133 between one afternoon and the next.
 */
function LinkBase({ url }: { url: string }) {
  const fragile = BARE_IP.test(url);
  const local = /localhost|127\.0\.0\.1/.test(url);
  return (
    <div className="linkbase">
      <h3>Slack links point to</h3>
      <p>
        <span className="mono">{url}</span>
      </p>
      {fragile ? (
        <p className="linkbase__warn">
          That is a bare IP address, which your router can reassign — every link already sent would
          then be dead. A hostname such as <span className="mono">machine-name.local</span>, or a
          fixed address from IT, survives the change.
        </p>
      ) : local ? (
        <p className="linkbase__warn">
          Only this machine can open a <span className="mono">localhost</span> link. Colleagues
          clicking a Slack digest will get nothing.
        </p>
      ) : null}
    </div>
  );
}

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

        {automation ? <LinkBase url={automation.public_app_url} /> : null}

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
