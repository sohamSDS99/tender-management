import type { AutomationStatus } from '../types';
import { Icon } from './Icon';

/**
 * A degraded automation state has to be visible without opening anything, but it
 * is one line rather than a panel: the bidder can do nothing about it, so it must
 * inform without taking over the page.
 */
export function Notice({ automation }: { automation: AutomationStatus | null }) {
  if (!automation) return null;
  const { slack, last_run: last } = automation;

  // First, and deliberately: while sweeps are off nothing else on this page is
  // going to change, so no other warning is more useful than this one.
  if (!automation.scheduler_in_process) {
    return (
      <p className="notice notice--bad" role="status">
        <Icon name="block" size={14} />
        Automated sweeps are paused, so no new tenders are being collected. Switch them back on in
        the sources section at the bottom of this page.
      </p>
    );
  }
  if (automation.scheduler_in_process && !automation.scheduler_running) {
    return (
      <p className="notice notice--bad" role="status">
        <Icon name="warn" size={14} />
        The scheduler is switched on but is not running, so no sweep will happen. Restart the API.
      </p>
    );
  }
  if (slack.status === 'degraded') {
    return (
      <p className="notice notice--bad" role="status">
        <Icon name="warn" size={14} />
        The last Slack digest did not send. Everything found is stored and safe, and the next sweep
        will announce it again.
      </p>
    );
  }
  if (last && last.sources_failed > 0) {
    return (
      <p className="notice" role="status">
        <Icon name="warn" size={14} />
        {last.sources_failed} of {last.sources_total} sources failed in the last sweep
        {last.errors.length ? ` (${last.errors.map((e) => e.source).join(', ')})` : ''}. Everything
        else came through.
      </p>
    );
  }
  if (slack.status === 'unconfigured') {
    return (
      <p className="notice" role="status">
        <Icon name="warn" size={14} />
        {/* min_score is null until Slack is configured; printing a literal 70
            stated a threshold the system had explicitly said it did not know. */}
        Slack alerts are off
        {slack.min_score !== null ? <> for tenders scoring {slack.min_score} or more</> : null}, so
        new high-scoring notices appear here only.
      </p>
    );
  }
  return null;
}
