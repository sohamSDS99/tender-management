import { useState } from 'react';
import type { SourceStatus } from '../../types';
import { api } from '../../api/client';
import { sourceHealth } from '../../labels';
import { Icon } from '../Icon';
import { SourceCard } from '../SourceCard';
import { AddSource } from './AddSource';
import { SettingsPage, SettingsRow, SettingsSection } from './SettingsPage';

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
  const [editing, setEditing] = useState<string | null>(null);
  const [value, setValue] = useState('');
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const keyed = sources.filter((s) => s.requires_api_key);

  const save = async (name: string) => {
    setSaving(true);
    setError(null);
    try {
      await api.setCredential(name, value);
      setEditing(null);
      setValue('');
      onChanged();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not save the key.');
    } finally {
      setSaving(false);
    }
  };
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
              detailed
            />
          ))}
        </div>
      </SettingsSection>

      {error ? (
        <p className="notice notice--bad" role="status">
          {error}
        </p>
      ) : null}

      <SettingsSection
        title="Credentials"
        note="Set here, a key takes effect on the next sweep — no .env edit, no restart."
      >
        {keyed.length === 0 ? (
          <p className="snote">
            <Icon name="info" size={14} />
            <span>None of the enabled sources needs an API key.</span>
          </p>
        ) : (
          keyed.map((source) => (
            <SettingsRow
              key={source.name}
              label={source.display_name}
              hint={
                source.credential_configured
                  ? `A key ending ${source.credential_hint ?? '····'} is in place.`
                  : 'No key set — this source is skipped until one is.'
              }
            >
              {editing === source.name ? (
                <div className="credrow">
                  <input
                    className="input input--sm"
                    type="password"
                    autoComplete="off"
                    placeholder="Paste the key"
                    value={value}
                    onChange={(event) => setValue(event.target.value)}
                  />
                  <button
                    type="button"
                    className="btn btn--primary btn--sm"
                    disabled={saving}
                    onClick={() => void save(source.name)}
                  >
                    {saving ? 'Saving…' : 'Save'}
                  </button>
                  <button
                    type="button"
                    className="btn btn--ghost btn--sm"
                    onClick={() => {
                      setEditing(null);
                      setValue('');
                    }}
                  >
                    Cancel
                  </button>
                </div>
              ) : (
                <button
                  type="button"
                  className="btn btn--sm"
                  onClick={() => {
                    setEditing(source.name);
                    setValue('');
                  }}
                >
                  {source.credential_configured ? 'Replace key' : 'Add key'}
                </button>
              )}
            </SettingsRow>
          ))
        )}
        <p className="snote">
          <Icon name="info" size={14} />
          <span>
            A key is stored and never shown again — only its last four characters, so you can tell
            which one is in place. Clearing the field falls back to <span className="mono">.env</span>.
            SAM.gov keys are free at{' '}
            <a href="https://sam.gov/content/api-keys" target="_blank" rel="noreferrer noopener">
              sam.gov/content/api-keys
            </a>
            .
          </span>
        </p>
      </SettingsSection>

      <AddSource onAdded={onChanged} />
    </SettingsPage>
  );
}
