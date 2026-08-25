import type { AutomationStatus, Stats } from '../../types';
import { formatDateTime } from '../../labels';
import { Icon } from '../Icon';
import { LinkBase } from '../LinkBase';
import { SettingsPage, SettingsSection } from './SettingsPage';

const SLACK_STATE: Record<string, { word: string; tone: string }> = {
  ok: { word: 'Delivering', tone: 'good' },
  degraded: { word: 'Last digest rejected', tone: 'bad' },
  unconfirmed: { word: 'Unconfirmed delivery', tone: 'bad' },
  disabled: { word: 'Switched off', tone: 'quiet' },
  unconfigured: { word: 'Not configured', tone: 'quiet' },
};

const TRANSPORT: Record<string, string> = {
  bot_token: 'Bot token (chat.postMessage)',
  webhook: 'Incoming webhook',
  none: 'None — nothing can be delivered',
};

/**
 * Where the digest goes, where its links point, and what the schedule resolves
 * to in UTC.
 *
 * Read-only on purpose. Everything here is deployment configuration that arrives
 * through the environment, and a browser is the wrong place to edit a Slack
 * token or a public URL — this product has no accounts, so anyone on the network
 * would be able to (D18). What the page owes the reader instead is the truth
 * about what is currently in force, because every one of these fails *silently*:
 * a wrong link base sends dead links to a channel and nothing on screen would
 * otherwise say so.
 */
export function SystemSettings({
  automation,
  stats,
  onBack,
}: {
  automation: AutomationStatus | null;
  stats: Stats | null;
  onBack: () => void;
}) {
  const slack = automation?.slack;
  const state = slack ? (SLACK_STATE[slack.status] ?? { word: slack.status, tone: 'quiet' }) : null;

  return (
    <SettingsPage
      title="System"
      blurb="What this deployment is currently doing. Read-only — these values come from the environment, not from the browser."
      onBack={onBack}
    >
      <SettingsSection
        title="Slack digest"
        note="A tender is announced at most once per channel, for all time — enforced by a unique constraint, not by convention."
      >
        {slack && state ? (
          <dl className="sfacts">
            <div>
              <dt>Status</dt>
              <dd>
                <span
                  className={`badge badge--${state.tone === 'good' ? 'green' : state.tone === 'bad' ? 'red' : 'grey'}`}
                >
                  {state.word}
                </span>
              </dd>
            </div>
            <div>
              <dt>Channel</dt>
              <dd className="mono">{slack.channel_label ?? '—'}</dd>
            </div>
            <div>
              <dt>Transport</dt>
              <dd>{TRANSPORT[slack.transport] ?? slack.transport}</dd>
            </div>
            <div>
              <dt>Announce at or above</dt>
              <dd className="num">{slack.min_score ?? '—'}</dd>
            </div>
            <div>
              <dt>Delivered so far</dt>
              <dd className="num">{slack.sent_total.toLocaleString('en-GB')}</dd>
            </div>
            <div>
              <dt>Unconfirmed</dt>
              <dd className="num">{slack.unconfirmed}</dd>
            </div>
          </dl>
        ) : (
          <p className="muted">Waiting for the API.</p>
        )}
        {slack?.detail ? (
          <p className="snote snote--warn">
            <Icon name="warn" size={14} />
            <span>{slack.detail}</span>
          </p>
        ) : null}
      </SettingsSection>

      <SettingsSection title="Links">
        {automation ? <LinkBase url={automation.public_app_url} /> : null}
      </SettingsSection>

      <SettingsSection
        title="Schedule in force"
        note="The local rule is the source of truth; the UTC form is derived from it, never hand-written."
      >
        {automation ? (
          <dl className="sfacts">
            <div>
              <dt>Timezone</dt>
              <dd className="mono">{automation.timezone}</dd>
            </div>
            <div>
              <dt>Local hours</dt>
              <dd className="num">
                {automation.run_hours_local
                  .map((h) => `${String(h).padStart(2, '0')}:00`)
                  .join(', ')}
              </dd>
            </div>
            <div>
              <dt>As UTC cron</dt>
              <dd className="mono">{automation.cron_utc.join('   ')}</dd>
            </div>
            <div>
              <dt>Observes DST</dt>
              <dd>{automation.observes_dst ? 'Yes — the cron drifts twice a year' : 'No'}</dd>
            </div>
            <div>
              <dt>Next sweep</dt>
              <dd className="num">
                {automation.scheduler_in_process ? automation.next_run_local_label : 'paused'}
              </dd>
            </div>
            <div>
              <dt>Scheduler registered here</dt>
              <dd>{automation.scheduler_running ? 'Yes' : 'No'}</dd>
            </div>
          </dl>
        ) : (
          <p className="muted">Waiting for the API.</p>
        )}
      </SettingsSection>

      <SettingsSection title="Data">
        <dl className="sfacts">
          <div>
            <dt>Notices stored</dt>
            <dd className="num">{(stats?.total_tenders ?? 0).toLocaleString('en-GB')}</dd>
          </div>
          <div>
            <dt>Last successful fetch</dt>
            <dd className="num">{formatDateTime(stats?.last_successful_fetch ?? null)}</dd>
          </div>
          <div>
            <dt>Score bands</dt>
            <dd className="num">
              {stats
                ? `possible ${stats.score_bands.possible_fit} · good ${stats.score_bands.good_fit} · excellent ${stats.score_bands.excellent_fit}`
                : '—'}
            </dd>
          </div>
        </dl>
      </SettingsSection>
    </SettingsPage>
  );
}
