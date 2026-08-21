import type { FetchRun } from '../types';
import { formatDateTime, runTone } from '../labels';

const DOT: Record<string, string> = {
  good: 'dot--good',
  warning: 'dot--warning',
  critical: 'dot--critical',
  idle: 'dot--brand',
};

/** Recent per-source runs. Read-only: nothing here can start a fetch. */
export function RunsTable({ runs }: { runs: FetchRun[] }) {
  if (runs.length === 0) return null;
  return (
    <details className="runs">
      <summary>Recent fetch runs</summary>
      <div className="runs__wrap">
        <table>
          <thead>
            <tr>
              <th>Source</th>
              <th>Status</th>
              <th>Trigger</th>
              <th>Received</th>
              <th>New</th>
              <th>Updated</th>
              <th>Skipped</th>
              <th>Started</th>
              <th>Message</th>
            </tr>
          </thead>
          <tbody>
            {runs.map((run) => (
              <tr key={run.id}>
                <td className="mono">{run.source}</td>
                <td>
                  <span className={`pill pill--${run.status}`}>
                    <span className={`dot ${DOT[runTone(run.status)]}`} />
                    {run.status}
                  </span>
                </td>
                <td className="mono">{run.trigger}</td>
                <td>{run.records_received}</td>
                <td>{run.records_created}</td>
                <td>{run.records_updated}</td>
                <td>{run.records_skipped}</td>
                <td>{formatDateTime(run.started_at)}</td>
                <td>{run.error_message?.slice(0, 160) ?? ''}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </details>
  );
}
