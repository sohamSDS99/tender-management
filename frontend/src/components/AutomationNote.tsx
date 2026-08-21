import type { AutomationStatus } from '../types';
import { Icon } from './Icon';

/**
 * A degraded automation state has to be visible without opening anything.
 *
 * Two things can go wrong quietly and must not: the sweep failed for some
 * sources, or the digest was not delivered. A Slack failure never rolls back
 * ingested data, which is exactly why it needs saying out loud here.
 */
export function AutomationNote({ automation }: { automation: AutomationStatus | null }) {
  if (!automation) return null;
  const { slack, last_run: last } = automation;

  // Intent without reality is the worst case: the header would promise a run
  // that nothing is going to fire.
  if (automation.scheduler_in_process && !automation.scheduler_running) {
    return (
      <p className="autonote autonote--bad" role="status">
        <Icon name="warning" size={14} />
        The scheduler is enabled but is not running in this process — no automated run will fire.
        Restart the API, or run the sweep from the runbook.
      </p>
    );
  }
  if (
    !automation.scheduler_in_process &&
    automation.scheduler_jobs.length === 0 &&
    !automation.last_run
  ) {
    return (
      <p className="autonote" role="status">
        <Icon name="warning" size={14} />
        No automated run has happened yet, and no scheduler is running here. Set{' '}
        <code>ENABLE_SCHEDULER=true</code> on the API, or let the GitHub Actions workflow own the
        schedule.
      </p>
    );
  }
  if (slack.status === 'degraded') {
    return (
      <p className="autonote autonote--bad" role="status">
        <Icon name="warning" size={14} />
        Slack digest not delivered — the tenders are stored and safe, and the next run will
        re-announce them. {slack.detail ? <span className="muted">{slack.detail}</span> : null}
      </p>
    );
  }
  if (last && last.sources_failed > 0) {
    return (
      <p className="autonote" role="status">
        <Icon name="warning" size={14} />
        {last.sources_failed} of {last.sources_total} sources failed in the last run
        {last.errors.length ? <>: {last.errors.map((e) => e.source).join(', ')}</> : null}.
        Everything else was ingested normally.
      </p>
    );
  }
  if (slack.status === 'unconfigured') {
    return (
      <p className="autonote" role="status">
        <Icon name="warning" size={14} />
        Slack notifications are off — set <code>SLACK_WEBHOOK_URL</code> to get a digest of new
        tenders scoring {slack.min_score ?? 70} or higher.
      </p>
    );
  }
  return null;
}
