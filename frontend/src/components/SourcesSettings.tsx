import { useState } from 'react';
import type { SourceStatus } from '../types';
import { api } from '../api/client';
import { formatDateTime } from '../labels';
import { Icon } from './Icon';

/**
 * Every connector the app has, always listed — including the ones switched off,
 * because a source you cannot see is a source you cannot turn back on.
 *
 * There is no "add a new source". connectors/registry.py hardcodes eight Python
 * classes with bespoke parsing per portal, so a new source is a module plus
 * tests, not a form. Offering one would promise something the architecture
 * cannot deliver.
 */
export function SourcesSettings({
  sources,
  busySource,
  onFetchSource,
  onChanged,
}: {
  sources: SourceStatus[];
  busySource: string | null;
  onFetchSource: (name: string) => void;
  onChanged: () => void;
}) {
  const [editing, setEditing] = useState<string | null>(null);
  const [value, setValue] = useState('');
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState<{ tone: 'ok' | 'bad'; text: string } | null>(null);

  const save = async (name: string) => {
    setSaving(true);
    setMessage(null);
    try {
      await api.setCredential(name, value);
      setEditing(null);
      setValue('');
      setMessage({ tone: 'ok', text: 'Key saved. It applies from the next sweep.' });
      onChanged();
    } catch (error) {
      setMessage({ tone: 'bad', text: error instanceof Error ? error.message : 'Could not save.' });
    } finally {
      setSaving(false);
    }
  };

  return (
    <section className="screen">
      <header className="screen__head">
        <h2>Sources</h2>
        <p>
          Eight connectors, each written against one portal&rsquo;s API. Switch them on or off in
          <code> .env</code>; keys set here take effect without a restart.
        </p>
      </header>

      {message ? (
        <p className={`notice${message.tone === 'bad' ? ' notice--bad' : ' notice--ok'}`} role="status">
          {message.text}
        </p>
      ) : null}

      <ul className="srclist">
        {sources.map((source) => {
          const broken = Boolean(source.unavailable_reason) || source.last_status === 'failed';
          return (
            <li key={source.name} className="srcrow">
              <div className="srcrow__main">
                <span className={`dot ${broken ? 'dot--critical' : 'dot--good'}`} aria-hidden="true" />
                <div className="srcrow__id">
                  <b>{source.display_name}</b>
                  <span>
                    {source.unavailable_reason
                      ? source.unavailable_reason
                      : `${source.tender_count.toLocaleString('en-GB')} stored · last run ${
                          source.last_run_at ? formatDateTime(source.last_run_at) : 'never'
                        }`}
                  </span>
                </div>
                <button
                  type="button"
                  className="btn btn--sm"
                  disabled={busySource !== null}
                  onClick={() => onFetchSource(source.name)}
                >
                  {busySource === source.name ? 'Fetching…' : 'Fetch now'}
                </button>
              </div>

              {source.requires_api_key ? (
                <div className="srcrow__cred">
                  <Icon name="sliders" size={13} />
                  {editing === source.name ? (
                    <>
                      <input
                        className="input input--sm"
                        type="password"
                        autoComplete="off"
                        placeholder="Paste the new key"
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
                    </>
                  ) : (
                    <>
                      <span className="srcrow__key">
                        {source.credential_configured
                          ? `Key ${source.credential_hint ?? '····'}`
                          : 'No key set'}
                      </span>
                      <button
                        type="button"
                        className="btn btn--sm"
                        onClick={() => {
                          setEditing(source.name);
                          setValue('');
                        }}
                      >
                        {source.credential_configured ? 'Replace' : 'Add key'}
                      </button>
                    </>
                  )}
                </div>
              ) : null}
            </li>
          );
        })}
      </ul>

      <p className="screen__foot">
        A key is stored, never shown again — only its last four characters, so you can tell which one
        is in place. Clearing the field falls back to the value in <code>.env</code>.
      </p>
    </section>
  );
}
