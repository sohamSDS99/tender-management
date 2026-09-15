import { useCallback, useEffect, useState } from 'react';
import type { AutomationStatus, SettingsSecrets, Stats } from '../../types';
import { api } from '../../api/client';
import { SecretField } from './SecretField';
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
 * Slack delivery is now editable here, which reverses part of an earlier
 * decision and is worth being explicit about. The reasoning against it stands:
 * this product has no accounts, so anyone on the company LAN can reach this
 * page (D18). What changed is that the alternative — editing .env and
 * recreating the container to rotate a token — meant a leaked bot token stayed
 * live until someone had shell access, which is worse.
 *
 * The exposure is bounded rather than removed: the token is write-only and
 * never returned, and writes answer to ALLOW_OPERATOR_ACTIONS. The residual
 * risk is someone on the LAN pointing the digest at a different channel, so
 * the channel in force is shown in full on this page rather than masked —
 * a redirect should be visible, not hidden behind dots.
 *
 * Everything else here stays read-only, because every one of those fails
 * *silently*: a wrong link base sends dead links to a channel and nothing on
 * screen would otherwise say so.
 */
export function SystemSettings({
  automation,
  stats,
  onReload,
  onBack,
}: {
  automation: AutomationStatus | null;
  stats: Stats | null;
  /** Re-read /api/automation, so a saved value's effect shows immediately. */
  onReload: () => void;
  onBack: () => void;
}) {
  const [secrets, setSecrets] = useState<SettingsSecrets>({});

  const loadSecrets = useCallback(() => {
    void api
      .settingsSecrets()
      .then(setSecrets)
      .catch(() => setSecrets({}));
  }, []);

  useEffect(loadSecrets, [loadSecrets]);

  const saved = () => {
    loadSecrets();
    onReload();
  };

  const at = (field: string) => secrets[field] ?? { configured: false, hint: null };
  const slack = automation?.slack;
  const state = slack ? (SLACK_STATE[slack.status] ?? { word: slack.status, tone: 'quiet' }) : null;

  return (
    <SettingsPage
      title="System"
      blurb="What this deployment is currently doing. Slack delivery can be set here; everything else comes from the environment."
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

      <SettingsSection
        title="Slack delivery"
        note="Set here, these beat .env and take effect immediately — no restart. The token is stored write-only and never shown again."
      >
        <SecretField
          field="slack_bot_token"
          label="Bot user OAuth token"
          hint="From the Slack app's OAuth & Permissions page. Needs chat:write, plus chat:write.public to post to a channel the bot has not joined."
          placeholder="xoxb-…"
          secret
          configured={at('slack_bot_token').configured}
          current={at('slack_bot_token').hint}
          onSaved={saved}
        />
        <SecretField
          field="slack_channel_id"
          label="Channel ID"
          hint="The ID, not the name — a name breaks the day someone renames the channel. Shown in full on purpose, so a redirect is visible."
          placeholder="C0123ABCDEF"
          configured={at('slack_channel_id').configured}
          current={at('slack_channel_id').hint}
          onSaved={saved}
        />
        <SecretField
          field="slack_bot_username"
          label="Posts as"
          hint="Worth setting: a Slack app is often created for something else and its bot user is named accordingly. Needs chat:write.customize."
          placeholder="Tender Monitor"
          configured={at('slack_bot_username').configured}
          current={at('slack_bot_username').hint}
          onSaved={saved}
        />
        <SecretField
          field="slack_webhook_url"
          label="Incoming webhook"
          hint="An alternative to the token. The token wins when both are set, because a webhook URL is its own credential and cannot be rotated."
          placeholder="https://hooks.slack.com/services/…"
          secret
          configured={at('slack_webhook_url').configured}
          current={at('slack_webhook_url').hint}
          onSaved={saved}
        />
        <SecretField
          field="slack_channel_label"
          label="Channel label"
          hint="Shown in the digest, and used as the ledger key: changing it makes every tender in the lookback window eligible to be announced once more."
          placeholder="#tenders"
          configured={at('slack_channel_label').configured}
          current={at('slack_channel_label').hint}
          onSaved={saved}
        />
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
