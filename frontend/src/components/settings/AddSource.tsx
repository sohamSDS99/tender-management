import { useState } from 'react';
import type { ProbeResult } from '../../types';
import { api } from '../../api/client';
import { Icon } from '../Icon';
import { SettingsRow, SettingsSection } from './SettingsPage';

/** The five fields a record must supply to become a tender. */
const FIELDS: { key: string; label: string; required?: boolean; hint: string }[] = [
  {
    key: 'source_notice_id',
    label: 'Notice ID',
    required: true,
    hint: 'Half the dedupe key — without it every sweep re-adds everything',
  },
  {
    key: 'title',
    label: 'Title',
    required: true,
    hint: 'Scored, displayed, and the main thing matched against',
  },
  { key: 'description', label: 'Description', hint: 'Most of the relevance signal lives here' },
  { key: 'source_url', label: 'Link to the notice', hint: 'How anyone acts on a result' },
  { key: 'deadline', label: 'Deadline', hint: 'Drives Closing soon and the digest urgency' },
  { key: 'buyer_name', label: 'Buyer', hint: '' },
  { key: 'buyer_country', label: 'Country', hint: '' },
  { key: 'publication_date', label: 'Published', hint: '' },
];

const slug = (value: string) =>
  value
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '_')
    .replace(/^_+|_+$/g, '')
    .slice(0, 64);

/**
 * Add a source by pointing at its API.
 *
 * The probe runs before anything is saved and reports what *parsed*, not what
 * answered — a 200 with no notices in it is not a working source, and letting
 * one be saved is how you get a feed that silently returns nothing forever.
 *
 * OCDS and RSS feeds need no mapping: the app already knows those schemas.
 * Anything else shows the paths found in the real response, so the mapping is
 * a matter of picking from what is actually there rather than guessing.
 */
export function AddSource({ onAdded }: { onAdded: () => void }) {
  const [open, setOpen] = useState(false);
  const [url, setUrl] = useState('');
  const [displayName, setDisplayName] = useState('');
  const [auth, setAuth] = useState<'none' | 'query' | 'header' | 'bearer'>('none');
  const [authParam, setAuthParam] = useState('api_key');
  const [credential, setCredential] = useState('');
  const [mapping, setMapping] = useState<Record<string, string>>({});
  const [probe, setProbe] = useState<ProbeResult | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const needsMapping = probe?.format === 'json';
  const mapped = FIELDS.filter((f) => f.required).every((f) => mapping[f.key]);
  const canSave = probe?.ok === true && displayName.trim().length > 0 && (!needsMapping || mapped);

  const reset = () => {
    setUrl('');
    setDisplayName('');
    setAuth('none');
    setCredential('');
    setMapping({});
    setProbe(null);
    setError(null);
  };

  const runProbe = async () => {
    setBusy(true);
    setError(null);
    setProbe(null);
    try {
      const result = await api.probeSource({
        url: url.trim(),
        auth,
        auth_param: authParam || null,
        credential,
        mapping: Object.keys(mapping).length
          ? { ...mapping, records: mapping.records ?? '' }
          : null,
      });
      setProbe(result);
      if (result.records_path) setMapping((prev) => ({ ...prev, records: result.records_path! }));
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not reach that endpoint.');
    } finally {
      setBusy(false);
    }
  };

  const save = async () => {
    setBusy(true);
    setError(null);
    try {
      await api.addSource({
        name: slug(displayName),
        display_name: displayName.trim(),
        url: url.trim(),
        auth,
        auth_param: auth === 'none' ? null : authParam,
        format: probe?.format ?? 'json',
        mapping: needsMapping ? mapping : null,
        credential,
      });
      reset();
      setOpen(false);
      onAdded();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not save that source.');
    } finally {
      setBusy(false);
    }
  };

  if (!open) {
    return (
      <SettingsSection
        title="Add a source"
        note="Point the app at any procurement API. It is tried before it is saved."
      >
        <button type="button" className="btn btn--primary" onClick={() => setOpen(true)}>
          <Icon name="download" size={14} />
          Add a source
        </button>
      </SettingsSection>
    );
  }

  return (
    <SettingsSection
      title="Add a source"
      note="Nothing is saved until the endpoint answers with notices."
    >
      {error ? (
        <p className="notice notice--bad" role="status">
          {error}
        </p>
      ) : null}

      <SettingsRow label="Name" hint="How it appears in the list and on every result.">
        <input
          className="input input--sm"
          value={displayName}
          placeholder="Kenya Tenders"
          onChange={(event) => setDisplayName(event.target.value)}
        />
      </SettingsRow>

      <SettingsRow label="Endpoint" hint="The URL that returns notices. Must be https.">
        <input
          className="input input--sm input--wide"
          value={url}
          placeholder="https://tenders.example.gov/api/v1/notices"
          onChange={(event) => setUrl(event.target.value)}
        />
      </SettingsRow>

      <SettingsRow label="Authentication" hint="How the key is sent, if one is needed.">
        <div className="seg seg--sm" role="group" aria-label="Authentication">
          {(['none', 'query', 'header', 'bearer'] as const).map((value) => (
            <button
              key={value}
              type="button"
              className={auth === value ? 'is-on' : undefined}
              aria-pressed={auth === value}
              onClick={() => setAuth(value)}
            >
              {value === 'none'
                ? 'None'
                : value === 'query'
                  ? 'Query'
                  : value === 'header'
                    ? 'Header'
                    : 'Bearer'}
            </button>
          ))}
        </div>
      </SettingsRow>

      {auth !== 'none' ? (
        <>
          {auth !== 'bearer' ? (
            <SettingsRow
              label={auth === 'query' ? 'Parameter name' : 'Header name'}
              hint={auth === 'query' ? 'e.g. api_key' : 'e.g. X-Api-Key'}
            >
              <input
                className="input input--sm"
                value={authParam}
                onChange={(event) => setAuthParam(event.target.value)}
              />
            </SettingsRow>
          ) : null}
          <SettingsRow label="API key" hint="Stored write-only. Never shown again after saving.">
            <input
              className="input input--sm"
              type="password"
              autoComplete="off"
              value={credential}
              onChange={(event) => setCredential(event.target.value)}
            />
          </SettingsRow>
        </>
      ) : null}

      <div className="screen__actions">
        <button
          type="button"
          className="btn"
          disabled={busy || !url.trim()}
          onClick={() => void runProbe()}
        >
          {busy ? 'Trying…' : probe ? 'Try again' : 'Try this endpoint'}
        </button>
        <button
          type="button"
          className="btn btn--ghost"
          onClick={() => {
            reset();
            setOpen(false);
          }}
        >
          Cancel
        </button>
      </div>

      {probe ? <ProbeReport probe={probe} /> : null}

      {probe && needsMapping ? (
        <>
          <h3 className="screen__section">Map the fields</h3>
          <p className="screen__hint">
            These are the paths found in the actual response. Point the two required ones at the
            right fields and the rest are optional.
          </p>
          <ul className="tunelist">
            {FIELDS.map((field) => (
              <li key={field.key}>
                <label htmlFor={`m-${field.key}`}>
                  {field.label}
                  {field.required ? ' *' : ''}
                  {field.hint ? <span>{field.hint}</span> : null}
                </label>
                <select
                  id={`m-${field.key}`}
                  className="select select--sm"
                  value={mapping[field.key] ?? ''}
                  onChange={(event) =>
                    setMapping((prev) => ({ ...prev, [field.key]: event.target.value }))
                  }
                >
                  <option value="">— not mapped —</option>
                  {(probe.paths ?? []).map((p) => (
                    <option key={p.path} value={p.path}>
                      {p.path} — {p.sample}
                    </option>
                  ))}
                </select>
              </li>
            ))}
          </ul>
        </>
      ) : null}

      {probe?.ok ? (
        <div className="screen__actions">
          <button
            type="button"
            className="btn btn--primary"
            disabled={busy || !canSave}
            title={canSave ? undefined : 'Map the required fields and give the source a name'}
            onClick={() => void save()}
          >
            {busy ? 'Saving…' : 'Add this source'}
          </button>
        </div>
      ) : null}
    </SettingsSection>
  );
}

function ProbeReport({ probe }: { probe: ProbeResult }) {
  if (!probe.ok) {
    return (
      <p className="notice notice--bad" role="status">
        <b>{probe.status ? `${probe.status} · ` : ''}</b>
        {probe.detail ?? 'That endpoint did not return anything usable.'}
      </p>
    );
  }
  return (
    <p className="notice notice--ok" role="status">
      <b>{probe.status} OK</b> · {probe.found} notices found
      {probe.parsed !== null && probe.parsed !== undefined ? ` · ${probe.parsed} parsed` : ''}
      {probe.format && probe.format !== 'json'
        ? ` · recognised as ${probe.format.toUpperCase()}, no mapping needed`
        : ''}
    </p>
  );
}
