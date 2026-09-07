import type { SourceStatus } from '../../types';
import { sourceHealth, type SourceVolumes } from '../../labels';
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
  volumes,
  busySource,
  onFetchSource,
  onChanged,
  onBack,
}: {
  sources: SourceStatus[];
  /** Per-source volume verdicts, so a silently-empty source is not listed healthy. */
  volumes: SourceVolumes;
  busySource: string | null;
  onFetchSource: (name: string) => void;
  /** Re-read /api/sources, so a saved key's hint appears without a reload. */
  onChanged: () => void;
  onBack: () => void;
}) {
  const health = (source: SourceStatus) => sourceHealth(source, volumes[source.name]);
  const broken = sources.filter((s) => health(s) === 'critical');
  const sweeping = sources.filter((s) => health(s) === 'sweeping');
  const healthy = sources.filter((s) => health(s) === 'good');
  const quiet = sources.filter((s) => health(s) === 'quiet');

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
          <span className="sstat__n num">{quiet.length}</span> quiet
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
        {quiet.length > 0 ? (
          <p className="snote snote--warn">
            <Icon name="warn" size={14} />
            <span>
              {quiet.map((s) => s.display_name).join(', ')} {quiet.length === 1 ? 'has' : 'have'}{' '}
              returned nothing in recent scheduled sweeps, though{' '}
              {quiet.length === 1 ? 'it normally returns' : 'they normally return'} more. That can
              be a real lull, or the feed may have changed shape. Open the source below to compare.
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
