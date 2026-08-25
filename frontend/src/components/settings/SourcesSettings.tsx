import type { SourceStatus } from '../../types';
import { sourceHealth } from '../../labels';
import { Icon } from '../Icon';
import { SourceCard } from '../SourceCard';
import { AddSource } from './AddSource';
import { SettingsPage, SettingsSection } from './SettingsPage';

/**
 * Every connector, why it is or is not working, and how to re-run one.
 *
 * The dashboard keeps its collapsed health strip — "where is this data coming
 * from" is a question a reader has on arrival, not one they go looking for
 * (D20). This page answers the other question: something is broken, what and
 * why. So it shows the notes, the last successful run and the key requirement
 * that the strip deliberately leaves out.
 *
 * The per-source fetch matters more than it looks. A single connector recovering
 * is the common case after a key or an outage is fixed, and re-running all eight
 * to test one costs thirteen minutes against eight public services.
 */
export function SourcesSettings({
  sources,
  busySource,
  onFetchSource,
  onChanged,
  onBack,
}: {
  sources: SourceStatus[];
  busySource: string | null;
  onFetchSource: (name: string) => void;
  /** Re-read /api/sources, so a saved key's hint appears without a reload. */
  onChanged: () => void;
  onBack: () => void;
}) {
  const broken = sources.filter((s) => sourceHealth(s) === 'critical');
  const sweeping = sources.filter((s) => sourceHealth(s) === 'sweeping');
  const healthy = sources.filter((s) => sourceHealth(s) === 'good');

  return (
    <SettingsPage
      title="Sources"
      blurb="Eight free public procurement feeds. One failing never fails a sweep — each gets its own run, so the rest still come through."
      onBack={onBack}
    >
      <SettingsSection title="At a glance">
        <p className="sstat">
          <span className="sstat__n num">{healthy.length}</span> healthy
          <span className="sstat__sep">·</span>
          <span className="sstat__n num">{sweeping.length}</span> sweeping
          <span className="sstat__sep">·</span>
          <span className="sstat__n num">{broken.length}</span> unavailable
          <span className="sstat__sep">·</span>
          <span className="sstat__n num">
            {sources.reduce((n, s) => n + s.tender_count, 0).toLocaleString('en-GB')}
          </span>{' '}
          notices stored in total
        </p>
        {broken.length > 0 ? (
          <p className="snote snote--warn">
            <Icon name="warn" size={14} />
            <span>
              {broken.map((s) => s.display_name).join(', ')}{' '}
              {broken.length === 1 ? 'cannot run' : 'cannot run'} until the configuration below is
              fixed. Every other source is unaffected.
            </span>
          </p>
        ) : null}
      </SettingsSection>

      <SettingsSection
        title="Connectors"
        note="Stored counts are everything ever ingested from that source, not the current view."
      >
        <div className="sources__grid">
          {sources.map((source) => (
            <SourceCard
              key={source.name}
              source={source}
              busySource={busySource}
              onFetch={onFetchSource}
              onCredentialSaved={onChanged}
              detailed
            />
          ))}
        </div>
      </SettingsSection>

      <AddSource onAdded={onChanged} />
    </SettingsPage>
  );
}
