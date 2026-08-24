import type { FetchRun } from '../types';
import { formatDateTime } from '../labels';

const PILL: Record<string, string> = {
  success: 'pill--success',
  partial: 'pill--partial',
  failed: 'pill--failed',
  skipped: 'pill--skipped',
  running: 'pill--running',
  queued: 'pill--running',
};

/**
 * Recent sweeps, collapsed.
 *
 * One row per source per sweep, because that is how they actually run — a single
 * connector failing does not fail the sweep, and a table that collapsed them into
 * one row per sweep would have to call a seven-of-eight success a failure.
 */
export function RunsTable({
  runs,
  sourceLabel,
}: {
  runs: FetchRun[];
  sourceLabel: (key: string) => string;
}) {
  if (runs.length === 0) return null;

  return (
    <details className="runs">
      <summary>Recent sweeps</summary>
      <div className="runs__wrap">
        <table>
          <thead>
            <tr>
              <th>Source</th>
              <th>Result</th>
              <th>Seen</th>
              <th>New</th>
              <th>Updated</th>
              <th>Started</th>
            </tr>
          </thead>
          <tbody>
            {runs.slice(0, 12).map((run) => (
              <tr key={run.id}>
                <td>{sourceLabel(run.source)}</td>
                <td>
                  <span className={`pill ${PILL[run.status] ?? 'pill--skipped'}`}>
                    {run.status}
                  </span>
                </td>
                <td>{run.records_received.toLocaleString('en-GB')}</td>
                <td>{run.records_created.toLocaleString('en-GB')}</td>
                <td>{run.records_updated.toLocaleString('en-GB')}</td>
                <td>{formatDateTime(run.started_at)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </details>
  );
}
