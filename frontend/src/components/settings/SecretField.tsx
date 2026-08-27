import { useState } from 'react';
import { SettingsRow } from './SettingsPage';
import { api } from '../../api/client';

/**
 * One operator-settable value, on the same rail as a source API key: stored in
 * app_settings, beats .env, applies without a restart.
 *
 * Secret values are write-only — set once, shown afterwards only as their last
 * four characters. Non-secret ones (a channel ID, a display name) show in full,
 * because hiding them would only stop the reader checking what is in force.
 */
export function SecretField({
  field,
  label,
  hint,
  placeholder,
  secret = false,
  configured,
  current,
  onSaved,
}: {
  field: string;
  label: string;
  hint?: string;
  placeholder?: string;
  /** Masked on screen and never returned by the API once stored. */
  secret?: boolean;
  configured: boolean;
  current: string | null;
  onSaved: () => void;
}) {
  const [editing, setEditing] = useState(false);
  const [value, setValue] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const save = async (next: string) => {
    setBusy(true);
    setError(null);
    try {
      await api.setSettingsSecret(field, next);
      setEditing(false);
      setValue('');
      onSaved();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not save.');
    } finally {
      setBusy(false);
    }
  };

  return (
    <SettingsRow label={label} hint={error ?? hint}>
      {editing ? (
        <div className="credrow">
          <input
            className="input input--sm"
            type={secret ? 'password' : 'text'}
            autoComplete="off"
            placeholder={placeholder}
            aria-label={label}
            value={value}
            onChange={(event) => setValue(event.target.value)}
          />
          <button
            type="button"
            className="btn btn--primary btn--sm"
            disabled={busy}
            onClick={() => void save(value)}
          >
            {busy ? 'Saving…' : 'Save'}
          </button>
          <button
            type="button"
            className="btn btn--ghost btn--sm"
            onClick={() => {
              setEditing(false);
              setValue('');
              setError(null);
            }}
          >
            Cancel
          </button>
        </div>
      ) : (
        <div className="credrow">
          <span className="src__keyhint">
            {configured
              ? secret
                ? `Set · ${current ?? '····'}`
                : current
              : 'Not set — falls back to .env'}
          </span>
          <button type="button" className="btn btn--sm" onClick={() => setEditing(true)}>
            {configured ? 'Replace' : 'Set'}
          </button>
          {configured ? (
            <button
              type="button"
              className="btn btn--ghost btn--sm"
              disabled={busy}
              onClick={() => void save('')}
            >
              Clear
            </button>
          ) : null}
        </div>
      )}
    </SettingsRow>
  );
}
