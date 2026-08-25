import type { AutomationStatus } from '../../types';
import { ScheduleEditor } from '../ScheduleEditor';
import { TriggerSwitch } from '../TriggerSwitch';
import { Icon } from '../Icon';
import { SettingsPage, SettingsSection } from './SettingsPage';

/**
 * Whether the sweep runs, and when.
 *
 * Both controls are the shipped, tested ones (D19, D21) and their behaviour is
 * deliberately unchanged by the move — they were passed into the old panel as
 * children and they are passed in here the same way.
 *
 * What this page adds is the thing the old panel never said: **where the sweep
 * depth lives, and why it is not here.** It is on the Fetch button in the
 * masthead on purpose (D24), because it decides whether pressing that button can
 * find anything at all, and it was being ignored precisely because nobody could
 * see it. Somebody looking for it in Settings needs to be told, not left
 * hunting.
 */
export function AutomationSettings({
  automation,
  onSaved,
  onBack,
}: {
  automation: AutomationStatus | null;
  onSaved: () => void;
  onBack: () => void;
}) {
  const paused = automation ? !automation.scheduler_in_process : false;

  return (
    <SettingsPage
      title="Automation"
      blurb="When the collector runs on its own. Changes apply to the running scheduler immediately — no restart."
      onBack={onBack}
    >
      <SettingsSection
        title="Scheduled sweeps"
        note="Pausing asks twice; resuming does not. A system that quietly stopped collecting is the kind of thing nobody notices for a week."
      >
        <TriggerSwitch automation={automation} onSaved={onSaved} />
      </SettingsSection>

      <SettingsSection
        title="Sweep times"
        note="Local hours in the configured timezone. The UTC equivalents are shown as you pick, because that mapping is only ever obviously right when it is written out."
      >
        <ScheduleEditor automation={automation} onSaved={onSaved} />
      </SettingsSection>

      <SettingsSection title="Search depth">
        <p className="snote">
          <Icon name="info" size={14} />
          <span>
            How far back a sweep looks is set <b>on the Fetch button</b> at the top of the
            dashboard, not here. It is the one number that decides whether pressing Fetch can find
            anything — the schedule already covers the last 72 hours twice a day, so a shallow
            manual sweep re-reads a window that holds nothing new. Keeping it beside the button is
            what makes that visible.
            {automation ? (
              <>
                {' '}
                The current default is <b>{automation.operator_fetch_days_back} days</b>.
              </>
            ) : null}
          </span>
        </p>
      </SettingsSection>

      {paused ? (
        <p className="snote snote--warn">
          <Icon name="block" size={14} />
          <span>
            Sweeps are paused right now, so nothing is being collected on a schedule and no Slack
            digest will be sent. The Fetch button still works for a one-off sweep.
          </span>
        </p>
      ) : null}
    </SettingsPage>
  );
}
