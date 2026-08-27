import { useState } from 'react';
import type { SourceStatus } from '../types';
import { api } from '../api/client';
import { formatWhen, sourceHealth, type SourceHealth } from '../labels';

export const SOURCE_CARD_CLASS: Record<SourceHealth, string> = {
  good: ' src--good',
  warning: ' src--warning',
  critical: ' src--critical',
  idle: ' src--idle',
  sweeping: ' src--sweeping',
};

/**
 * One connector, with its state said in words as well as colour.
 *
 * Shared by the dashboard's collapsed health strip and the Sources settings
 * page. They answer different questions — the strip is "where is this data
 * coming from" on arrival (D20), the page is "what is wrong and how do I fix
 * it" — but a source card is a source card, and two copies would drift the
 * first time one of them learned something the other did not.
 */
export function SourceCard({
  source,
  busySource,
  onFetch,
  onCredentialSaved,
  detailed = false,
}: {
  source: SourceStatus;
  /** Name of the source currently fetching, so only its button is pending. */
  busySource: string | null;
  onFetch: (name: string) => void;
  /** Re-read /api/sources so a saved key's hint appears without a reload. */
  onCredentialSaved?: () => void;
  /** The settings page shows the notes and the last success; the strip does not. */
  detailed?: boolean;
}) {
  const [editing, setEditing] = useState(false);
  const [value, setValue] = useState('');
  const [saving, setSaving] = useState(false);
  const [keyError, setKeyError] = useState<string | null>(null);

  const saveKey = async () => {
    setSaving(true);
    setKeyError(null);
    try {
      await api.setCredential(source.name, value);
      setEditing(false);
      setValue('');
      onCredentialSaved?.();
    } catch (error) {
      setKeyError(error instanceof Error ? error.message : 'Could not save the key.');
    } finally {
      setSaving(false);
    }
  };

  const state = sourceHealth(source);
  const status = source.unavailable_reason
    ? 'unavailable'
    : state === 'sweeping'
      ? 'sweeping now'
      : (source.last_status ?? 'never run');

  return (
    <article className={`src${SOURCE_CARD_CLASS[state]}`}>
      <header>
        <span className="src__name">{source.display_name}</span>
        <span className="src__status">{status}</span>
      </header>

      <p className="src__meta">
        {source.tender_count.toLocaleString('en-GB')} stored
        {source.last_run_at ? ` · ${formatWhen(source.last_run_at)}` : ' · never run'}
        {source.keyword_prefiltered ? ' · keyword prefilter applied' : ''}
      </p>

      {/*
        Only a *current* problem is an alarm. last_error is what happened on the
        last run, which may be days old and already fixed — a stored key clears
        unavailable_reason, but the skipped run that predates it stays on record
        forever. Rendering that in red made a working source look broken.
      */}
      {source.unavailable_reason ? (
        <p className="src__err">{source.unavailable_reason}</p>
      ) : source.last_error ? (
        <p className="src__was">
          {source.last_run_at ? `${formatWhen(source.last_run_at)}: ` : ''}
          {source.last_error.slice(0, 160)}
        </p>
      ) : null}

      {detailed ? (
        <>
          {source.notes ? <p className="src__notes">{source.notes}</p> : null}
          <p className="src__meta">
            {source.last_success_at
              ? `Last successful run ${formatWhen(source.last_success_at)}`
              : 'No successful run yet'}
            {/* Only when one is actually missing. Saying "needs an API key"
                beside a key that is set reads as the key not having worked. */}
            {source.requires_api_key && !source.credential_configured ? ' · needs an API key' : ''}
            {!source.enabled ? ' · switched off in configuration' : ''}
          </p>

          {/*
            The key lives on the source it belongs to, not in a separate list.
            A standalone Credentials section stayed on screen forever once every
            key was set, saying nothing anyone needed — and it put the control
            one place away from the source it acts on.
          */}
          {source.requires_api_key ? (
            <div className="src__key">
              {editing ? (
                <>
                  <input
                    className="input input--sm"
                    type="password"
                    autoComplete="off"
                    placeholder="Paste the key"
                    aria-label={`API key for ${source.display_name}`}
                    value={value}
                    onChange={(event) => setValue(event.target.value)}
                  />
                  <button
                    type="button"
                    className="btn btn--primary btn--sm"
                    disabled={saving}
                    onClick={() => void saveKey()}
                  >
                    {saving ? 'Saving…' : 'Save'}
                  </button>
                  <button
                    type="button"
                    className="btn btn--ghost btn--sm"
                    onClick={() => {
                      setEditing(false);
                      setValue('');
                      setKeyError(null);
                    }}
                  >
                    Cancel
                  </button>
                </>
              ) : (
                <>
                  <span className="src__keyhint">
                    {source.credential_configured
                      ? `Key ····${(source.credential_hint ?? '').replace(/^…/, '')}`
                      : 'No key set'}
                  </span>
                  <button type="button" className="btn btn--sm" onClick={() => setEditing(true)}>
                    {source.credential_configured ? 'Replace' : 'Add key'}
                  </button>
                </>
              )}
            </div>
          ) : null}
          {keyError ? <p className="src__err">{keyError}</p> : null}
        </>
      ) : null}

      <div className="src__foot">
        <button
          type="button"
          className="btn btn--sm"
          disabled={
            Boolean(source.unavailable_reason) ||
            !source.enabled ||
            source.running ||
            busySource !== null
          }
          onClick={() => onFetch(source.name)}
          title={
            source.unavailable_reason
              ? 'This source cannot run until its configuration is fixed'
              : `Query ${source.display_name} now`
          }
        >
          {source.running || busySource === source.name ? 'Fetching…' : 'Fetch this source'}
        </button>
        {detailed ? (
          <a className="src__home" href={source.homepage} target="_blank" rel="noreferrer noopener">
            Open {source.display_name}
          </a>
        ) : null}
      </div>
    </article>
  );
}
